import os
import threading
import io
import sys
import pylibmc
import re
import hashlib
import urllib2
import logging
import subprocess
import cherrypy
from mako.template import Template

if not os.path.exists("/tmp/pytube"):
    os.mkdir("/tmp/pytube")
logging.basicConfig(filename="/tmp/pytube/log", level=logging.DEBUG)
logging.info("")
logging.info("-----")
logging.info("")

DOMAIN = "http://aquageek.net/y"

_mc = pylibmc.Client(["127.0.0.1"], binary=True)
_mcpool = pylibmc.ThreadMappedPool(_mc)

def mcget(key):
    with _mcpool.reserve() as mc:
        return mc.get(key)

def mcset(key, value, time=None):
    with _mcpool.reserve() as mc:
        if time != None:
            return mc.set(key, value, time)
        else:
            return mc.set(key, value)

re_vid = re.compile(".*v=([^&]+)")
re_token = re.compile('.*"t": "([^"]+)"')
re_fmtlist = re.compile("fmt_list=([^&]+)")

cacheprefix=1

def cache(func):
    global cacheprefix
    cacheprefix+=1
    prefix=cacheprefix
    def cached(*args, **kwargs):
        queryhash=hashlib.md5(str(prefix)+repr(args)+repr(kwargs)).hexdigest()
        logging.debug('Getting '+queryhash+" from memcached")
        with _mcpool.reserve() as mc:
            data = mc.get(queryhash)
            if data == None:
                logging.debug("Not found")
                data = func(*args, **kwargs)
            mc.set(queryhash, data, 60*30)
        logging.debug(queryhash+": "+repr(data)[:150])
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


@cache
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

@cache
def videourl(vid, audiovideo="video"):
    info=videoinfo("v="+vid)
    if audiovideo=="audio":
        fmt=info["bestfmt"][1]
    else:
        fmt=info["bestfmt"][0]
    if info == None:
        logging.critical("Oh no! Aborting")
        return None
    url = "http://www.youtube.com/get_video?asv=&video_id="+info["vid"]+"&t="+info["token"]+"&fmt="+fmt
    return url

def videofile(vid, audiovideo="video"):
    url = videourl(vid, audiovideo)
    video = urllib2.urlopen(url)
    return video

def getvideodata(vid, audiovideo="video", blocksize=1024):
    remote = videofile(vid, audiovideo)
    vidlen = int(remote.info()["Content-length"])
    datatotal=0
    prct = -1
    while True:
        prevprct = prct
        data = remote.read(blocksize)
        datalen = len(data)
        if datalen == 0:
            logging.info("end of download")
            break
        else:
            datatotal += datalen
            prct = datatotal * 100 / vidlen
            mcset("status"+vid, str(prct), 20)
            if prevprct < prct:
                logging.info(vid+": "+str(prct)+"%")
            yield data

devnull = io.open("/dev/null", "wb")

def save_mp3(vid):
    info = videoinfo("v="+vid)
    filename = "/tmp/pytube/"+vid+".mp3"
    FFMPEG="ffmpeg -i pipe:0 -vn -acodec libmp3lame -f mp3 -y "+filename
    ffmpeg = subprocess.Popen(FFMPEG, shell=True, stdin=subprocess.PIPE, stdout=devnull, stderr=devnull)
    for videodata in getvideodata(info["vid"], audiovideo="audio"):
        ffmpeg.stdin.write(videodata)
    mcset("status"+vid, "done")
    return filename

class Serve:
    @cherrypy.expose
    def index(self):
        html = io.open("index.html").read()
        return html

    @cherrypy.expose
    def status(self, vid="NvlW4bEjB5A", _=None):
        status = mcget("status"+vid)
        if status == None:
            mcset("status"+vid, "0", 120)
            t=threading.Thread(target=save_mp3, args=(vid,))
            t.start()
            return "processing..."
        elif status == "done":
            return "done"
        else:
            return "still processing : "+status+"% done"

    @cherrypy.expose
    def dl(self, url="v=NvlW4bEjB5A"):
        if url == "": url="v=NvlW4bEjB5A"
        vid = videoinfo(url)["vid"]
        status = mcget("status"+vid)
        if status == None or (status == "done" and not os.path.exists("/tmp/pytube/"+vid+".mp3")):
            mcset("status"+vid, "0", 60)
            t=threading.Thread(target=save_mp3, args=(vid,))
            t.start()
            statusstr = "downloading and processing"
        elif status == "done":
            statusstr = "<a href='"+DOMAIN+"/y/dlm/"+vid+".mp3'>done</a>"
        else:
            statusstr = "still processing : "+status+"% done"
        html = Template(filename="status.html").render(vid=vid, status=statusstr)
        return html



#if __name__ == "__main__" and len(sys.argv) > 1:
    #info=videoinfo(sys.argv[1])
    #save_mp3(info["vid"])
#elif __name__ == "__main__":
    #print "No url was supplied on the command line, exiting..."

if __name__ == "__main__":
    root = Serve()
    cherrypy.config.update("config")
    app=cherrypy.tree.mount(root, "/", "config")

    cherrypy.engine.start()
    cherrypy.engine.block()
