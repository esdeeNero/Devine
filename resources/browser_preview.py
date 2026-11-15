"""Ephemeral browser-only preview. No screen capture, files, or model context."""
import base64,io,threading,time,uuid
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"python-libs"))
from PIL import Image
class BrowserPreview:
 def __init__(self):
  self.lock=threading.RLock();self.stop_event=None;self.state={'id':None,'active':False,'revision':0};self.ready=False
 def begin(self,engine,capture,allowed,cancel,chat=None):
  with self.lock:
   if self.state['active'] and self.state.get('engine')==engine:return
   self.finish()
   stop=threading.Event();self.stop_event=stop;self.ready=False
   self.state={'id':uuid.uuid4().hex,'active':True,'engine':engine,'chat':chat,'revision':0,'title':'Браузер','url':'','action':'Открывает страницу…','image':None,'error':None}
   def loop():
    while not stop.wait(1.2):
     if cancel.is_set() or not allowed():
      with self.lock:
       if not stop.is_set():self.finish('Остановлено')
      return
     with self.lock:ready=self.ready
     if not ready:continue
     try:
      result=capture()
      if not result or not result.get('data'):continue
      with Image.open(io.BytesIO(base64.b64decode(result['data']))) as im:
       im.thumbnail((960,640));out=io.BytesIO();im.convert('RGB').save(out,format='JPEG',quality=72)
      frame='data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()
      with self.lock:
       if stop.is_set():return
       self.state.update(image=frame,revision=self.state['revision']+1,error=None)
     except Exception:
      with self.lock:
       if not stop.is_set():self.state['error']='Ждём изображение браузера…'
   threading.Thread(target=loop,daemon=True,name='browser-preview').start()
 def action(self,name):
  labels={'browser_open':'Открывает страницу…','browser_observe':'Изучает страницу…','browser_click':'Нажимает на элемент…','browser_type':'Вводит текст…','browser_scroll':'Прокручивает страницу…','browser_snapshot':'Смотрит страницу…'}
  with self.lock:self.state.update(action=labels.get(name,'Работает в браузере…'),error=None)
 def observed(self,result):
  with self.lock:
   if result.get('error'):self.state['error']=str(result['error'])[:180];return
   self.ready=True
   for key in ['title','url']:
    if result.get(key):self.state[key]=str(result[key])[:2000]
 def finish(self,label='Завершено'):
  with self.lock:
   if self.stop_event:self.stop_event.set()
   self.state.update(active=False,action=label);self.ready=False
 def snapshot(self):
  with self.lock:return self.state.copy()
preview=BrowserPreview()
