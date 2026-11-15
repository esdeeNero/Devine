"""Devine's local agent tools. All execution is mediated by the selected policy."""
import os,sys,json,time,uuid,hashlib,difflib,threading,subprocess,signal,tempfile,shutil,socket,ipaddress,urllib.request,urllib.parse
from pathlib import Path
from html.parser import HTMLParser
from collections import deque
sys.path.insert(0,str(Path(__file__).parent/'python-libs'))
C=None
LOCAL=threading.local()
NATIVE_TOKEN=os.environ.get('DEVINE_NATIVE_TOKEN','')
native_jobs={};native_lock=threading.RLock();native_status={'screen':False,'accessibility':False,'connected':False}
agent_runner=None
ACTIVE_BROWSER_ENGINE=None
from browser_preview import preview as browser_preview

def context(**values):
    for k,v in values.items():setattr(LOCAL,k,v)
def ctx(key,default=None):return getattr(LOCAL,key,default)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def settings():return C.settings()
def permission(group):return bool(settings().get('permissions',{}).get(group,False))

BROWSER={'browser_open','browser_observe','browser_click','browser_type','browser_scroll','browser_snapshot'}
READ={'list_directory','read_file','search_files'}
WRITE={'write_file','edit_file','make_directory','move_to_trash','restore_backup','create_presentation','create_document','create_spreadsheet'}
NETWORK={'web_fetch','search_web'}
COMPUTER={'computer_click','computer_type','computer_key','computer_scroll','computer_press','activate_app','open_url','inspect_screen'}

def group_for(name):
    if name in BROWSER:return 'browser'
    if name in READ|WRITE or name=='open_in_editor':return 'files'
    if name=='run_command':return 'terminal'
    if name in NETWORK:return 'network'
    if name=='capture_screen':return 'screen'
    if name in COMPUTER:return 'computer'
    if name=='run_agent':return 'files'
    raise ValueError('Неизвестный инструмент: '+name)

def checked(raw,create=False):
    s=settings();root=ctx('project')
    raw=raw or root or '~'
    p=Path(raw).expanduser()
    if not p.is_absolute():p=Path(root or Path.home())/p
    p=p.resolve(strict=not create)
    if s['access']=='attachments':raise PermissionError('Доступ к файлам отключён. Выберите рабочую папку или разрешите доступ в настройках.')
    if s['access']=='folders' and not any(p.is_relative_to(Path(r).resolve()) for r in s.get('roots',[])):raise PermissionError('Путь находится вне разрешённых папок')
    if create:
        parent=p.parent
        while not parent.exists():parent=parent.parent
        if not parent.is_dir():raise ValueError('Родительский путь не является папкой')
    return p

def brief(name,args):
    if name=='run_command':return args.get('command','')
    if name=='browser_type':return 'Поле '+str(args.get('id',''))+': '+str(args.get('text',''))[:240]
    if name=='browser_click':return 'Элемент '+str(args.get('id',''))
    if name=='computer_type':return 'Ввести текст: '+str(args.get('text',''))[:200]
    if name.startswith('computer_'):return json.dumps(args,ensure_ascii=False)
    return str(args.get('path') or args.get('url') or args.get('query') or args.get('name') or name)

def authorize(name,args,event,cancel,preview=None):
    group=group_for(name)
    if not permission(group):raise PermissionError('Инструмент отключён: '+group+'. Включите его в меню «Доступ и инструменты».')
    if name=='open_url' and not permission('network'):raise PermissionError('Открытие сайтов отключено. Включите «Интернет» в меню «Доступ и инструменты».')
    s=settings();mode=s.get('approval','ask')
    if mode=='full':ask=False
    elif mode=='auto':ask=name in NETWORK|COMPUTER|{'capture_screen','run_command','move_to_trash','restore_backup'} or (name in WRITE and s['access']=='full')
    else:ask=True
    if not ask:return
    rid=uuid.uuid4().hex;r={'event':threading.Event(),'allowed':False}
    with C.mutex:C.pending[rid]=r
    try:
        event({'permission':{'id':rid,'tool':name,'path':brief(name,args),'preview':(preview or '')[:16000],'kind':group}})
        for _ in range(1800):
            if r['event'].wait(.2):break
            if cancel.is_set():break
        if not r['allowed'] or cancel.is_set():raise PermissionError('Действие не разрешено пользователем')
    finally:
        with C.mutex:C.pending.pop(rid,None)
    # Settings can be revoked while approval was pending.
    if not permission(group):raise PermissionError('Разрешение отозвано')

def native_pending():
    with native_lock:
        native_status['connected']=True;native_status['last_seen']=time.time()
        for job in native_jobs.values():
            if not job.get('sent') and not job['event'].is_set():job['sent']=True;return {k:job[k] for k in ['id','action','args']}
    return None

def native_result(data):
    with native_lock:
        native_status.update(data.get('permissions',{}));native_status['connected']=True
        job=native_jobs.get(data.get('id'))
        if job:job['result']=data.get('result',{});job['event'].set()

def native_call(action,args,cancel):
    if not NATIVE_TOKEN:raise RuntimeError('Эта функция доступна в приложении Devine для macOS')
    if time.time()-native_status.get('last_seen',0)>10:raise RuntimeError('Нет связи с macOS. Перезапустите Devine.')
    rid=uuid.uuid4().hex;job={'id':rid,'action':action,'args':args,'event':threading.Event(),'sent':False}
    with native_lock:native_jobs[rid]=job
    try:
        for _ in range(300):
            if cancel.is_set():raise RuntimeError('Действие остановлено')
            if job['event'].wait(.2):
                result=job.get('result',{})
                if result.get('error'):raise RuntimeError(result['error'])
                return result
        raise TimeoutError('macOS не ответила на запрос')
    finally:
        with native_lock:native_jobs.pop(rid,None)

def backup(path):
    key=uuid.uuid4().hex;folder=C.DATA/'backups'/key;folder.mkdir(parents=True)
    exists=path.exists();meta={'id':key,'path':str(path),'existed':exists,'time':time.time(),'mode':path.stat().st_mode & 0o777 if exists else 0o644}
    if exists:
        if not path.is_file():raise ValueError('Можно изменять только обычный файл')
        if path.stat().st_size>20*1024*1024:raise ValueError('Файл больше 20 МБ')
        shutil.copy2(path,folder/'original')
    (folder/'meta.json').write_text(json.dumps(meta));return key

def atomic_write(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    mode=path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd,tmp=tempfile.mkstemp(prefix='.devine-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def register_artifact(path,event):
    entry={'id':uuid.uuid4().hex,'chat':ctx('chat',''),'path':str(path),'name':path.name,'size':path.stat().st_size,'time':time.time()}
    with C.mutex:
        f=C.DATA/'artifacts.json';items=json.loads(f.read_text()) if f.exists() else [];items.insert(0,entry);f.write_text(json.dumps(items[:500],ensure_ascii=False))
    event({'artifact':entry});return entry

def artifacts(chat=None):
    f=C.DATA/'artifacts.json';items=json.loads(f.read_text()) if f.exists() else []
    return [x for x in items if not chat or x.get('chat')==chat]

def run_command(args,event,cancel):
    cwd=checked(args.get('path') or ctx('project') or '~');command=str(args.get('command','')).strip()
    if not cwd.is_dir() or not command:raise ValueError('Укажите команду и рабочую папку')
    timeout=min(max(int(args.get('timeout',60)),1),180)
    env=os.environ.copy();env.pop('DEVINE_NATIVE_TOKEN',None)
    with tempfile.TemporaryFile() as log:
        p=subprocess.Popen(['/bin/zsh','-lc',command],cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+timeout;last='';stopped=False
        try:
            while p.poll() is None:
                if cancel.is_set() or time.monotonic()>deadline or os.fstat(log.fileno()).st_size>10_000_000:
                    os.killpg(p.pid,signal.SIGTERM);stopped=True
                    try:p.wait(3)
                    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL)
                    break
                text=os.pread(log.fileno(),64000,0).decode(errors='replace')[-16000:]
                if text!=last:event({'console':{'command':command,'text':text,'running':True}});last=text
                cancel.wait(.15)
            p.wait();text=os.pread(log.fileno(),100000,0).decode(errors='replace')[-20000:]
            event({'console':{'command':command,'text':text,'running':False,'code':p.returncode}})
            return {'exit_code':p.returncode,'output':text,'stopped':stopped}
        finally:
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.wait()

class ExtractHTML(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ['script','style','noscript']:self.skip+=1
        if tag in ['p','div','h1','h2','h3','li','br']:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ['script','style','noscript']:self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)

def safe_url(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Используйте публичный адрес http/https без пароля')
    for item in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)):
        if not ipaddress.ip_address(item[4][0]).is_global:raise PermissionError('Адреса локальной сети недоступны этому инструменту')
    return url
class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        safe_url(newurl);return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch(url):
    safe_url(url);req=urllib.request.Request(url,headers={'User-Agent':'Devine/2.0 (local assistant)'})
    with urllib.request.build_opener(Redirect).open(req,timeout=25) as r:
        raw=r.read(1_000_001)
        if len(raw)>1_000_000:raise ValueError('Страница больше 1 МБ')
        if 'text/' not in r.headers.get('Content-Type',''):raise ValueError('Этот адрес не является текстовой страницей')
        text=raw.decode(r.headers.get_content_charset() or 'utf-8',errors='replace')
        if 'html' in r.headers.get('Content-Type',''):parser=ExtractHTML();parser.feed(text);text=' '.join(parser.parts)
        return {'url':r.url,'text':text[:24000],'truncated':len(text)>24000}


def call(name,args,event,cancel):
    global ACTIVE_BROWSER_ENGINE
    if cancel.is_set():return {'error':'Остановлено'}
    try:
        group_for(name);path=None;preview='';old=None;new=None;expected=args.get('expected_sha256')
        if name in READ|WRITE|{'open_in_editor','run_command'}:
            if name=='restore_backup':
                bid=str(args.get('backup_id',''))
                if len(bid)!=32 or any(c not in '0123456789abcdef' for c in bid):raise ValueError('Некорректная резервная копия')
                meta=json.loads((C.DATA/'backups'/bid/'meta.json').read_text());path=checked(meta['path'],True)
            else:path=checked(args.get('path'),name in WRITE)
        if name in ['write_file','edit_file']:
            if path.exists() and (not path.is_file() or path.stat().st_size>2_000_000):raise ValueError('Можно менять текстовые файлы размером до 2 МБ')
            old=path.read_text() if path.exists() else ''
            if expected and (not path.exists() or digest(path)!=expected):raise ValueError('Файл изменился. Прочитайте его заново перед правкой.')
            if name=='write_file':
                if path.exists() and not args.get('overwrite',False):raise ValueError('Файл уже существует. Прочитайте его и используйте edit_file либо overwrite=true.')
                new=str(args.get('content',''))
            else:
                needle=str(args.get('old_text',''))
                if not needle or old.count(needle)!=1:raise ValueError('old_text должен точно и однозначно совпадать с одним фрагментом файла')
                new=old.replace(needle,str(args.get('new_text','')),1)
            if len(new.encode())>2_000_000:raise ValueError('Текст больше 2 МБ')
            preview=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=str(path),tofile=str(path)))
            if len(preview)>16000:preview=preview[:16000]+'\n…'
        if name=='run_command':preview=str(args.get('command',''))
        if name.startswith('create_'):preview=json.dumps(args,ensure_ascii=False,indent=2)[:15000]
        authorize(name,args,event,cancel,preview)
        if path is not None:path=checked(str(path),name in WRITE)
        event({'tool':{'name':name,'path':brief(name,args),'status':'running'}})
        if name in READ:
            # Reuse bounded, tested readers after applying the new policy once.
            if name=='read_file':
                if not path.is_file() or path.stat().st_size>C.MAX_FILE:raise ValueError('Ожидается файл до 20 МБ')
                text,short=C.extraction(path);result={'path':str(path),'text':text,'truncated':short,'sha256':digest(path)}
            elif name=='list_directory':
                if not path.is_dir():raise ValueError('Это не папка')
                result={'path':str(path),'entries':[]}
                with os.scandir(path) as it:
                    for e in it:
                        if len(result['entries'])>=200:break
                        if e.name.startswith('.') and not args.get('hidden'):continue
                        result['entries'].append({'name':e.name,'type':'link' if e.is_symlink() else 'folder' if e.is_dir(follow_symlinks=False) else 'file'})
                result['entries'].sort(key=lambda e:(e['type']!='folder',e['name'].lower()))
            else:
                query=str(args.get('query','')).strip().lower()
                if not query:raise ValueError('Укажите запрос')
                found=[];start=time.monotonic();count=0
                for folder,dirs,files in os.walk(path,followlinks=False):
                    dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ['node_modules','Library','System']];count+=1
                    found.extend(str(Path(folder)/n) for n in dirs+files if query in n.lower())
                    if len(found)>=100 or count>=1500 or time.monotonic()-start>4 or cancel.is_set():break
                result={'matches':found[:100],'bounded_search':True}
        elif name in ['write_file','edit_file']:
            # Reject conflicts introduced while the user was reviewing the preview.
            if (path.read_text() if path.exists() else '')!=old:raise ValueError('Файл изменился во время подтверждения. Прочитайте заново.')
            key=backup(path);atomic_write(path,new.encode());result={'path':str(path),'backup_id':key,'sha256':digest(path),'bytes':path.stat().st_size};register_artifact(path,event)
        elif name=='make_directory':path.mkdir(parents=True,exist_ok=True);result={'path':str(path),'created':True}
        elif name=='move_to_trash':
            if not path.is_file():raise ValueError('В корзину можно переместить только отдельный файл')
            key=backup(path);trash=Path.home()/'.Trash';trash.mkdir(exist_ok=True);target=trash/(uuid.uuid4().hex[:8]+'-'+path.name);shutil.move(str(path),target);result={'trash':str(target),'backup_id':key}
        elif name=='restore_backup':
            key=backup(path)
            if meta['existed']:atomic_write(path,(C.DATA/'backups'/bid/'original').read_bytes())
            elif path.exists():
                trash=Path.home()/'.Trash';trash.mkdir(exist_ok=True);shutil.move(str(path),trash/(uuid.uuid4().hex[:8]+'-'+path.name))
            result={'path':str(path),'restored':True,'backup_id':key}
        elif name=='run_command':result=run_command(args,event,cancel)
        elif name=='open_in_editor':
            if not path.exists():raise ValueError('Файл или папка не найдены')
            r=subprocess.run(['/usr/bin/open','-a','Visual Studio Code',str(path)],capture_output=True,timeout=10)
            if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace'))
            result={'opened':str(path),'app':'Visual Studio Code'}
        elif name in ['create_presentation','create_document','create_spreadsheet']:
            if path.exists() and not args.get('overwrite'):raise ValueError('Файл уже существует. Выберите новое имя либо overwrite=true.')
            from office_tools import build
            key=backup(path);path.parent.mkdir(parents=True,exist_ok=True)
            raw=build(name,args);atomic_write(path,raw);result=register_artifact(path,event);result['backup_id']=key
        elif name=='web_fetch':result=fetch(str(args.get('url','')))
        elif name=='search_web':
            query=str(args.get('query',''));data=fetch('https://html.duckduckgo.com/html/?q='+urllib.parse.quote(query));result={'query':query,**data}
        elif name=='run_agent':
            if not agent_runner:raise RuntimeError('Агент недоступен')
            result=agent_runner(args,event,cancel)
        elif name in BROWSER:
            engine=args.get('engine',settings().get('browser_engine','devine')) if name=='browser_open' else (ACTIVE_BROWSER_ENGINE or settings().get('browser_engine','devine'))
            if engine not in ['safari','devine']:raise ValueError('Неизвестный браузер')
            if name=='browser_open':ACTIVE_BROWSER_ENGINE=engine
            def capture_preview():
                if engine=='safari':
                    from safari_browser import browser
                    if not browser.lock.acquire(blocking=False):return None
                    try:
                        if not browser.session:return None
                        return {'data':browser.request('GET',browser.path('/screenshot'),timeout=3)}
                    finally:browser.lock.release()
                return native_call('browser_snapshot',{},cancel)
            browser_preview.begin(engine,capture_preview,lambda:permission('browser'),cancel,ctx('chat'))
            browser_preview.action(name)
            if engine=='safari':
                from safari_browser import browser
                browser.data=C.DATA;result=browser.perform(name,args,cancel)
            else:result=native_call(name,args,cancel)
            browser_preview.observed(result)
            if result.get('data'):
                a=C.upload({'name':'Браузер-'+time.strftime('%H-%M-%S')+'.jpg','data':result.pop('data')});result['attachment']=a;event({'screenshot':{'attachment':a}})
        elif name=='capture_screen':
            result=native_call(name,{},cancel)
            if result.get('data'):
                a=C.upload({'name':'Экран-'+time.strftime('%H-%M-%S')+'.jpg','data':result.pop('data')});result['attachment']=a;event({'screenshot':{'attachment':a,'width':result.get('width'),'height':result.get('height')}})
        else:result=native_call(name,args,cancel)
        C.audit(name,args,'allowed');event({'tool':{'name':name,'path':brief(name,args),'status':'done'}});return result
    except Exception as e:
        if name in BROWSER:browser_preview.observed({'error':str(e)})
        C.audit(name,args,'denied: '+str(e));event({'tool':{'name':name,'path':brief(name,args),'status':'error','error':str(e)}});return {'error':str(e)}

def spec(name,desc,props,required):return {'type':'function','function':{'name':name,'description':desc,'parameters':{'type':'object','properties':props,'required':required}}}
S={'type':'string'};B={'type':'boolean'};I={'type':'integer'}
TOOLS=[
 spec('list_directory','List local files and directories. Use real tool results; never invent filenames.',{'path':S,'hidden':B},['path']),
 spec('read_file','Read a text/PDF/DOCX file and its sha256 hash.',{'path':S},['path']),
 spec('search_files','Find filenames under a directory (bounded recursive search).',{'path':S,'query':S},['path','query']),
 spec('write_file','Create a UTF-8 file. Existing files need overwrite=true. A backup and review diff are created.',{'path':S,'content':S,'overwrite':B,'expected_sha256':S},['path','content']),
 spec('edit_file','Replace exactly one literal old_text fragment. Read file first; expected_sha256 detects conflicts.',{'path':S,'old_text':S,'new_text':S,'expected_sha256':S},['path','old_text','new_text']),
 spec('make_directory','Create a directory within permitted locations.',{'path':S},['path']),
 spec('move_to_trash','Move one file to macOS Trash, with a backup; never permanent deletion.',{'path':S},['path']),
 spec('restore_backup','Restore a file from a Devine backup_id.',{'backup_id':S},['backup_id']),
 spec('run_command','Run a shell command in a working directory, get output and exit code. Commands use user privileges and are not sandboxed. No sudo, no destructive bulk operations.',{'path':S,'command':S,'timeout':I},['path','command']),
 spec('open_in_editor','Open a created file or project in Visual Studio Code.',{'path':S},['path']),
 spec('web_fetch','Read a public HTTP/HTTPS webpage. Cite its URL in your answer.',{'url':S},['url']),
 spec('search_web','Search the web via DuckDuckGo HTML. Results may be blocked by provider; report any failure honestly.',{'query':S},['query']),
 spec('capture_screen','Capture the main display. Requires screen permission. The resulting screenshot is provided visually on your next turn.',{},[]),
 spec('inspect_screen','Read the accessibility tree of the frontmost app. Requires Accessibility permission.',{},[]),
 spec('browser_open','Open a website in Safari or Devine, according to engine or user preference. Safari uses an isolated Apple WebDriver automation window and needs Allow Remote Automation. Returns real page content and element ids. Never pretend that setup succeeded.',{'url':S,'engine':{'type':'string','enum':['safari','devine']}},['url']),
 spec('browser_observe','Read fresh state of the active controlled browser, visible element ids and video playback state.',{},[]),
 spec('browser_click','Click an element id from the latest browser observation. Agent cursor animates visibly. Returns updated page. Never invent ids.',{'id':I},['id']),
 spec('browser_type','Fill a visible text field by latest element id. Optionally submit a search form. Passwords are manual.',{'id':I,'text':S,'submit':B},['id','text']),
 spec('browser_scroll','Scroll active controlled browser. Positive pixels scroll down; negative up. Returns fresh visible elements.',{'pixels':I},['pixels']),
 spec('browser_snapshot','Get a screenshot of the active controlled browser for visual inspection. No system screen permission needed.',{},[]),
 spec('open_url','Open an HTTP/HTTPS URL specifically in Safari. Requires Internet and Computer tools. This confirms opening only, not successful page load or video playback; inspect_screen or capture_screen must verify the page and Play/Pause state.',{'url':S},['url']),
 spec('activate_app','Bring a macOS app to front by application name.',{'name':S},['name']),
 spec('computer_click','Click using pixel coordinates of the most recent screenshot. Capture a fresh screenshot before deciding next action.',{'x':I,'y':I,'button':{'type':'string','enum':['left','right']},'double':B},['x','y']),
 spec('computer_press','Press an accessibility element by its id from the latest inspect_screen result.',{'id':I},['id']),
 spec('computer_type','Type text into the currently focused UI field.',{'text':S},['text']),
 spec('computer_key','Press a key or shortcut, e.g. cmd+s, cmd+n, return, tab, escape. Observe screen after actions.',{'key':S},['key']),
 spec('computer_scroll','Scroll the active app. Positive pixels scroll up; negative scroll down.',{'pixels':I},['pixels']),
 spec('run_agent','Delegate a bounded code task to planner, coder or reviewer. Agents run sequentially using the loaded model.',{'name':{'type':'string','enum':['planner','coder','reviewer']},'task':S},['name','task']),
 spec('create_presentation','Create a real PPTX presentation. slides have title, subtitle, bullets (up to 6 short items), layout cover/content/two-column, optional right_bullets, notes. Use one idea per slide.',{'path':S,'title':S,'slides':{'type':'array','items':{'type':'object','properties':{'title':S,'subtitle':S,'bullets':{'type':'array','items':S},'right_bullets':{'type':'array','items':S},'layout':S,'notes':S},'required':['title']}},'overwrite':B},['path','title','slides']),
 spec('create_document','Create a real DOCX with title and sections (heading, body or bullets).',{'path':S,'title':S,'sections':{'type':'array','items':{'type':'object','properties':{'heading':S,'body':S,'bullets':{'type':'array','items':S}}}},'overwrite':B},['path','title','sections']),
 spec('create_spreadsheet','Create a formatted XLSX with sheet name, headers and rows (values or formulas).',{'path':S,'sheet':S,'headers':{'type':'array','items':S},'rows':{'type':'array','items':{'type':'array','items':{}}},'overwrite':B},['path','headers','rows'])
]

def available():
    result=[]
    for tool in TOOLS:
        name=tool['function']['name']
        if not permission(group_for(name)):continue
        if name=='open_url' and not permission('network'):continue
        if name in READ|WRITE|{'run_command','open_in_editor','run_agent'} and settings()['access']=='attachments':continue
        result.append(tool)
    return result
