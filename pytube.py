import os
import io
import sys
import pylibmc
import re
import hashlib
import itertools
import threading
import urllib2
import logging
#import cherrypy
#import pymedia #later

logging.basicConfig(filename="/tmp/ytd.log", level=logging.DEBUG)
logging.info("")
logging.info("-----")
logging.info("")


mc = pylibmc.Client(["127.0.0.1"], binary=True)
mcpool = pylibmc.ThreadMappedPool(mc)

re_vid = re.compile(".*v=([^&]+)")
re_token = re.compile('.*"t": "([^"]+)"')
re_fmtlist = re.compile("fmt_list=([^&]+)")

cacheprefix=0
def cache(func):
    global cacheprefix
    cacheprefix+=1
    prefix=cacheprefix
    def cached(*args, **kwargs):
        queryhash=hashlib.md5(str(prefix)+repr(args)+repr(kwargs)).hexdigest()
        logging.debug('Getting '+queryhash+" from memcached")
        with mcpool.reserve() as mc:
            data = mc.get(queryhash)
            if data == None:
                logging.debug("Not found")
                data = func(*args, **kwargs)
                mc.set(queryhash, data, 60*5)
        logging.debug(queryhash+": "+repr(data)[0:150])
        return data
    return cached

@cache
def urlread(url, timeout=20):
    return urllib2.urlopen(url, timeout=timeout).read()


@cache
def bestfmt(fmtlist):
    FMT_PRIORITY = ["38", "37", "45", "22", "43", "35", "18", "34", "5", "17"]
    AUDIO_FMT_PRIORITY = ["38", "45", "43", "17", "34", "18", "35", "22", "37", "5"]
    logging.debug(fmtlist)
    for i in FMT_PRIORITY:
        if i in fmtlist:
            best=i
            break
    for i in AUDIO_FMT_PRIORITY:
        if str(i) in fmtlist:
            audiobest=i
            break
    return [best, audiobest]


#@cache
def videoinfo(url):
  _vid = re_vid.search(url)
  if _vid == None:
      logging.critical("URL is invalid")
      return None
  vid = _vid.group(1)
  logging.info("video id is "+repr(vid))
  videohtml = urlread("http://www.youtube.com/watch?v="+vid, timeout=20)
  if videohtml == None:
      logging.critical("Could not connect to youtube servers")
      return None
  _token = re_token.search(videohtml)
  if _token == None:
      logging.critical("Could not find token")
      return None
  token = _token.group(1)
  logging.info("video token is "+token)
  __fmtlist=re_fmtlist.search(videohtml)
  _fmtlist=__fmtlist.group(1).split("%")
  fmtlist = [_fmtlist[0],]
  for i in range(0, len(_fmtlist), 5):
      if i!=0:
          fmtlist.append(_fmtlist[i][2:])
  logging.info("formats are "+ repr(fmtlist))

  return {"vid": vid, "fmtlist": fmtlist, "bestfmt": bestfmt(fmtlist), "token": token} # more metadata later

def videofile(url, audiovideo="video"):
    info=videoinfo(url)
    if audiovideo=="audio":
        fmt=info["bestfmt"][1]
    else:
        fmt=info["bestfmt"][0]
    if info == None:
        logging.critical("Oh no! Aborting")
        return None
    url = "http://www.youtube.com/get_video?asv=&video_id="+info["vid"]+"&t="+info["token"]+"&fmt="+fmt
    logging.info("Download URL is "+url)
    video = urllib2.urlopen(url)
    return video

def downloadvideo(url):
    remotevid = videofile(url)
    vidinfo = videoinfo(url)
    vidfile = io.open("/tmp/" + vidinfo["vid"], "bw")

    vidlen = int(remotevid.info()["Content-length"])
    blocksize=1024
    datatotal=0
    prct = 0
    while True:
        prevprct = prct
        data = remotevid.read(blocksize)
        datalen = len(data)
        if datalen == 0:
            logging.info("End of download")
            remotevid.close()
            vidfile.close()
            break
        else:
            datatotal += datalen
            prct = datatotal * 100 / vidlen
            if prevprct < prct:
                print(str(prct)+"%")
            vidfile.write(data)

if __name__ == "__main__" and len(sys.argv) > 1:
    downloadvideo(sys.argv[1])
    #videofile(sys.argv[1])
elif __name__ == "__main__":
    print "No url was supplied on the command line, exiting..."

