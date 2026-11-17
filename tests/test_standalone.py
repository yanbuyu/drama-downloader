import json,threading, subprocess,sys,tempfile,os,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from pathlib import Path
ROOT=Path(__file__).resolve().parent
# 离线动态生成测试用 MP4，避免仓库存放二进制视频资源。
import imageio_ffmpeg
_video_tmp = tempfile.TemporaryDirectory()
VIDEO_FILE = Path(_video_tmp.name) / "plain.mp4"
subprocess.run([
    imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=black:s=64x64:r=12:d=0.5",
    "-an", "-c:v", "mpeg4", str(VIDEO_FILE)
], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
class H(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def respond(self,data):
  b=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_POST(self):
  u=urlparse(self.path);n=int(self.headers.get('Content-Length','0'));data=self.rfile.read(n)
  if u.path=='/service/2/device_register/':
   assert data[:6]==bytes.fromhex('746305100000'),data[:8]
   return self.respond({'device_id_str':str(time.time_ns())[:18], 'install_id_str':str(time.time_ns())[1:18]})
  if u.path=='/novel/player/multi_video_detail/preload/v1':
   payload=json.loads(data)
   assert payload['series_id']=='10001',payload
   assert payload['dr_scene']=='preload',payload
   assert self.headers.get('x-argus'),self.headers
   return self.respond({'code':0,'data':{'10001':{'video_data':{
       'series_id_str':'10001','series_title':'测试短剧','episode_cnt':2,
       'series_intro':'测试简介','video_list':[
           {'vid':'20002','vid_index':2,'title':'结婚三年，女主遭到丈夫冷落，决定带着两个孩子离开原来的生活，重新开始'},
           {'vid':'20001','vid_index':1,'title':'结婚三年，女主遭到丈夫冷落，决定带着两个孩子离开原来的生活，重新开始'}
       ]}}}})
  if u.path=='/novel/player/video_detail/v1/':
   assert self.headers.get('x-argus'),self.headers
   return self.respond({'code':0,'data':{'video_data':{'series_id_str':'10001','series_title':'测试短剧','episode_cnt':2,'series_intro':'测试简介','celebrities':[{'nickname':'张三','role_name':'甲'}]}}})
  if u.path=='/novel/player/multi_video_model/player/v1':
   assert self.headers.get('x-medusa') and self.headers.get('x-helios')
   assert data[:2]==b'\x1f\x8b',data[:8]
   payload=json.loads(__import__('gzip').decompress(data))
   vid=payload['mixed_video_id_map']['1004'][0]
   port=self.server.server_port
   url=f'http://127.0.0.1:{port}/plain.mp4'
   model={'video_list':{'video_1':{'main_url':url,'vwidth':1280,'vheight':720,'codec_type':'h264'}}}
   return self.respond({'code':0,'data':{'1004':{vid:{'video_model':json.dumps(model)}}}})
  self.send_error(404)
 def do_GET(self):
  u=urlparse(self.path);q=parse_qs(u.query)
  if u.path=='/reading/bookapi/search/tab/v':
   assert self.headers.get('x-argus')
   return self.respond({'code':0,'data':{'search_tabs':[{'tab_type':11,'title':'短剧','data':[{'series_id_str':'10001','video_data':[{'video_id':'20001','series_title':'测试短剧','episode_cnt':2}]}]}]}})
  if u.path=='/reading/bookapi/directory/all_items/v':
   assert self.headers.get('x-argus')
   return self.respond({'code':0,'data':{'episodes':[{'video_id':'39901','episode_title':'第110集'},{'video_id':'39902','episode_title':'第111集'}]}})
  if u.path=='/plain.mp4':
   b=VIDEO_FILE.read_bytes();offset=0;rang=self.headers.get('Range','')
   if rang.startswith('bytes='):
    offset=int(rang[6:].split('-')[0]);self.send_response(206);self.send_header('Content-Range',f'bytes {offset}-{len(b)-1}/{len(b)}')
   else:self.send_response(200)
   b=b[offset:];self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
  self.send_error(404)
server=ThreadingHTTPServer(('127.0.0.1',0),H)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
with tempfile.TemporaryDirectory() as t:
 cmds=[['register'],['devices'],['search','测试'],['info','10001'],['download','10001','--episodes','1-2','--quality','720','--workers','2'],['download','10001','--episodes','1-2','--quality','720','--workers','2']]
 for tail in cmds:
  cmd=[sys.executable,str(ROOT.parent/'drama_downloader.py'),'--mock-origin',base,'--pool',t+'/pool.json','--devices','2','--out',t+'/output']+tail
  r=subprocess.run(cmd,capture_output=True,text=True,timeout=50)
  print('>>>',tail,'RETURN',r.returncode,'OUT',r.stdout[-900:],'ERR',r.stderr[-500:])
  assert r.returncode==0, (tail,r.stdout,r.stderr)
 assert (Path(t)/'output'/'测试短剧'/'001-第1集[720].mp4').is_file()
 assert (Path(t)/'output'/'测试短剧'/'002-第2集[720].mp4').is_file()
server.shutdown()

_video_tmp.cleanup()
