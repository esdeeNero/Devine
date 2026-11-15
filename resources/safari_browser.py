"""Safari WebDriver bridge. Uses only a dedicated Apple automation session."""
import atexit,base64,json,os,socket,subprocess,threading,time,urllib.request,urllib.error,urllib.parse
from pathlib import Path

class SafariBrowser:
 def __init__(self):self.process=None;self.port=None;self.session=None;self.lock=threading.RLock();self.data=None
 def request(self,method,path,payload=None,timeout=20):
  req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,method=method,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout) as r:data=json.load(r)
  except urllib.error.HTTPError as e:
   try:data=json.load(e)
   finally:e.close()
  value=data.get('value',{})
  if isinstance(value,dict) and value.get('error'):
   message=value.get('message',str(value['error']))
   if any(x in message.lower() for x in ['remote automation','allow remote','not authorized','enable']):
    raise RuntimeError('Safari требует разрешение автоматизации. В Safari включите настройки для веб-разработчиков, затем Разработка → Разрешить удалённую автоматизацию. Если macOS запросит пароль для safaridriver, введите его самостоятельно. Подробность Safari: '+message[:350])
   if value.get('error')=='invalid session id':self.session=None
   raise RuntimeError(message[:1000])
  return value
 def start(self):
  if self.process is None or self.process.poll() is not None:
   with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
   log=open(self.data/'safaridriver.log','ab') if self.data else subprocess.DEVNULL
   self.process=subprocess.Popen(['/usr/bin/safaridriver','-p',str(self.port)],stdin=subprocess.DEVNULL,stdout=log,stderr=log)
   if log is not subprocess.DEVNULL:log.close()
   for _ in range(40):
    if self.process.poll() is not None:raise RuntimeError('Не удалось запустить safaridriver')
    try:self.request('GET','/status',timeout=1);break
    except Exception:time.sleep(.1)
   else:raise RuntimeError('safaridriver не отвечает')
  if self.session is None:
   value=self.request('POST','/session',{'capabilities':{'alwaysMatch':{'browserName':'safari'}}},timeout=35)
   self.session=value['sessionId']
   self.request('POST',self.path('/timeouts'),{'pageLoad':20000,'script':10000,'implicit':0})
 def path(self,suffix):
  if not self.session:raise RuntimeError('Сначала откройте Safari через browser_open')
  return '/session/'+self.session+suffix
 def script(self,script,args=None):return self.request('POST',self.path('/execute/sync'),{'script':script,'args':args or []})
 def close(self):
  if self.session:
   try:self.request('DELETE',self.path(''),timeout=3)
   except Exception:pass
  self.session=None
  if self.process and self.process.poll() is None:
   self.process.terminate()
   try:self.process.wait(3)
   except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
  self.process=None
 def observe(self):
  return self.script('''window.__devineSafariNodes=[];const nodes=[];for(const e of document.querySelectorAll('a[href],button,input,textarea,select,summary,[role="button"],[role="link"],[contenteditable="true"],video')){const r=e.getBoundingClientRect(),s=getComputedStyle(e);if(!r.width||!r.height||r.bottom<0||r.top>innerHeight||s.visibility==='hidden'||s.display==='none')continue;const id=window.__devineSafariNodes.length;window.__devineSafariNodes.push(e);nodes.push({id,tag:e.tagName.toLowerCase(),type:e.type||'',label:(e.getAttribute('aria-label')||e.innerText||e.placeholder||e.title||'').slice(0,180),value:e.type==='password'?'[protected]':String(e.value||'').slice(0,160),href:e.href||'',x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});if(nodes.length>=140)break;}return {browser:'Safari',session:'isolated WebDriver automation window',url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,14000),nodes,media:[...document.querySelectorAll('video,audio')].map(v=>({paused:v.paused,ended:v.ended,time:v.currentTime,ready:v.readyState})),viewport:{width:innerWidth,height:innerHeight}};''')
 def element(self,index):
  return self.script('''const e=window.__devineSafariNodes?.[arguments[0]];if(!e?.isConnected)throw Error('Элемент устарел. Выполните browser_observe');if(e.type==='password')throw Error('Пароль вводит пользователь вручную');const label=e.getAttribute('aria-label')||e.innerText||e.placeholder||'';if(/купить|оплат|оформить заказ|удалить аккаунт|отправить сообщение|purchase|checkout|pay now|delete account|send message|captcha|sign in|log in|войти/i.test(label))throw Error('Нужно ручное действие пользователя: '+label);return e;''',[int(index)])['element-6066-11e4-a52e-4f735466cecf']
 def pointer(self,index):
  self.script('''const e=window.__devineSafariNodes?.[arguments[0]];if(!e?.isConnected)return;let p=document.getElementById('__devineSafariPointer');if(!p){p=document.createElement('div');p.id='__devineSafariPointer';p.setAttribute('aria-hidden','true');p.style.cssText='position:fixed;z-index:2147483647;pointer-events:none;width:24px;height:30px;background:#bc92ff;clip-path:polygon(0 0,100% 55%,60% 60%,80% 95%,59% 100%,40% 67%,13% 85%);filter:drop-shadow(0 1px 2px black);transition:left .22s ease,top .22s ease;left:70px;top:70px';document.documentElement.append(p);}const r=e.getBoundingClientRect();requestAnimationFrame(()=>{p.style.left=(r.x+r.width/2)+'px';p.style.top=(r.y+r.height/2)+'px'});''',[int(index)]);time.sleep(.25)
 def perform(self,action,args,cancel):
  with self.lock:
   if cancel.is_set():return {'error':'Остановлено пользователем'}
   if action=='browser_open':
    url=str(args.get('url',''));p=urllib.parse.urlparse(url)
    if p.scheme not in ['http','https'] or not p.hostname or p.username or p.password:raise ValueError('Нужен адрес http/https без пароля')
    self.start();self.request('POST',self.path('/url'),{'url':url},timeout=25);return self.observe()
   if not self.session:raise RuntimeError('Сначала browser_open с engine=safari')
   if action=='browser_observe':return self.observe()
   if action=='browser_snapshot':
    raw=self.request('GET',self.path('/screenshot'));return {'data':raw,'browser':'Safari'}
   if action=='browser_scroll':self.script('window.scrollBy(0,arguments[0]);',[max(-1600,min(1600,int(args.get('pixels',600))))]);time.sleep(.3);return self.observe()
   index=int(args.get('id',-1));eid=self.element(index);self.pointer(index)
   if cancel.is_set():return {'error':'Остановлено пользователем'}
   if action=='browser_click':self.request('POST',self.path('/element/'+eid+'/click'),{})
   elif action=='browser_type':
    self.request('POST',self.path('/element/'+eid+'/clear'),{})
    text=str(args.get('text',''))+('\ue007' if args.get('submit') else '')
    self.request('POST',self.path('/element/'+eid+'/value'),{'text':text})
   else:raise ValueError('Неизвестное действие Safari')
   time.sleep(.4);return self.observe()

browser=SafariBrowser()
atexit.register(browser.close)
