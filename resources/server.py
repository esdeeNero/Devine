import os, sys, json, time, uuid, sqlite3, threading, subprocess, shutil, signal, atexit, secrets
sys.dont_write_bytecode=True
from contextlib import contextmanager
from pathlib import Path
import capabilities as caps
import re
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
ROOT=Path(__file__).parent
DATA=Path.home()/"Library/Application Support/Local Chat"
DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
os.chmod(DATA,0o700)
caps.DATA=DATA
TOKEN=secrets.token_urlsafe(32)
PORT=int(sys.argv[1]) if len(sys.argv)>1 else 18743
MODEL_PORT=18744
lock=threading.RLock()
process=None
active=None
phase="off"
failure=""
cancel=threading.Event()
generating=False
ollama_process=None

@contextmanager
def db():
    c=sqlite3.connect(DATA/"chats.sqlite3", timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY,title TEXT,model TEXT,updated REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY,chat TEXT,role TEXT,content TEXT)")
    chat_columns=[r[1] for r in c.execute("PRAGMA table_info(chats)")]
    for column,default in [('mode','chat'),('agent','team'),('project','')]:
        if column not in chat_columns:c.execute(f"ALTER TABLE chats ADD COLUMN {column} TEXT DEFAULT '{default}'")
    c.execute("CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY,chat TEXT,name TEXT,task TEXT,status TEXT,content TEXT,updated REAL)")
    columns=[r[1] for r in c.execute("PRAGMA table_info(messages)")]
    if "attachments" not in columns:c.execute("ALTER TABLE messages ADD COLUMN attachments TEXT DEFAULT '[]'")
    if "reasoning" not in columns:c.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT DEFAULT ''")
    if "thinking_ms" not in columns:c.execute("ALTER TABLE messages ADD COLUMN thinking_ms INTEGER DEFAULT 0")
    try:
        with c:yield c
    finally:c.close()

def http(url,payload=None,timeout=5):
    req=Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers={"Content-Type":"application/json"})
    return urlopen(req,timeout=timeout)

def ollama(path,payload=None,timeout=5):
    with http("http://127.0.0.1:11434"+path,payload,timeout) as r:return json.load(r)

def models():
    found={}
    roots=[Path.home()/".cache/huggingface/hub",Path.home()/"Library/Caches/llama.cpp",Path.home()/".cache/llama.cpp",Path.home()/"Models"]
    custom=DATA/"folders.json"
    if custom.exists():
        roots += [Path(x) for x in json.loads(custom.read_text())]
    for root in roots:
        if not root.exists():continue
        for folder,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if d not in ["node_modules",".git","Local Chat.app","Devine.app"]]
            for name in files:
                if not name.lower().endswith(".gguf") or any(x in name.lower() for x in ["mmproj","projector"]):continue
                path=Path(folder)/name
                try:
                    real=str(path.resolve()); size=path.stat().st_size
                    projectors=list(path.parent.glob("*mmproj*.gguf"))
                    projector=str(projectors[0]) if len(projectors)==1 else None
                    found[real]={"id":real,"name":name[:-5],"size":round(size/1e9,1),"engine":"llama.cpp","path":str(path),"vision":bool(projector),"projector":projector}
                except OSError:pass
    try:
        for m in ollama("/api/tags").get("models",[]):
            key="ollama:"+m["name"]
            try:vision="vision" in ollama("/api/show",{"model":m["name"]}).get("capabilities",[])
            except Exception:vision=False
            found[key]={"id":key,"name":m["name"],"size":round(m.get("size",0)/1e9,1),"engine":"Ollama","vision":vision}
    except Exception:pass
    return sorted(found.values(),key=lambda m:m["name"])

catalog=models()

def external():
    result=[]
    try:
        lines=subprocess.check_output(["/bin/ps","-axo","pid=,comm="],text=True).splitlines()
        for line in lines:
            a=line.strip().split(None,1)
            if len(a)==2 and Path(a[1]).name in ("llama-cli","llama-server"):
                pid=int(a[0])
                if not process or pid!=process.pid:result.append(pid)
    except Exception:pass
    return result

def stop():
    global process,active,phase,failure
    cancel.set()
    with lock:
        p=process
        if p and p.poll() is None:
            p.terminate()
            try:p.wait(timeout=12)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        process=None
        if active and active["engine"]=="Ollama":
            try:ollama("/api/generate",{"model":active["name"],"keep_alive":0},30)
            except Exception as e:raise RuntimeError("Не удалось выгрузить Ollama: "+str(e))
        active=None;phase="off";failure=""

def start(mid):
    global process,active,phase,failure,ollama_process
    with lock:
        if phase=="loading":raise ValueError("Модель уже загружается")
        chosen=next((m for m in catalog if m["id"]==mid),None)
        if not chosen:raise ValueError("Модель не найдена. Обновите список.")
        if external():raise ValueError("Модель уже запущена в терминале. Остановите её кнопкой в панели состояния, затем нажмите «Запустить».")
        stop();active=chosen;phase="loading";failure="";cancel.clear()
        if chosen["engine"]=="llama.cpp":
            binary=shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
            log=open(DATA/"model.log","ab")
            try:process=subprocess.Popen([binary,"-m",chosen["path"],"--host","127.0.0.1","--port",str(MODEL_PORT),"-ngl","99","-c","16384","-fa","on","-ctk","q8_0","-ctv","q8_0","--parallel","1","--jinja"]+(["--mmproj",chosen["projector"],"--image-max-tokens","1024"] if chosen.get("projector") else []),stdout=log,stderr=log)
            except Exception as e:
                phase="error";failure=str(e);raise
            finally:log.close()
        target=active
    def ready():
        global phase,failure,ollama_process
        try:
            if target["engine"]=="Ollama":
                try:ollama("/api/tags")
                except Exception:
                    binary=shutil.which("ollama") or "/usr/local/bin/ollama"
                    log=open(DATA/"ollama.log","ab")
                    ollama_process=subprocess.Popen([binary,"serve"],stdout=log,stderr=log);log.close()
                    time.sleep(2)
                ollama("/api/generate",{"model":target["name"],"keep_alive":-1},240)
            else:
                for _ in range(480):
                    if active is not target:return
                    if process is None or process.poll() is not None:raise RuntimeError("Не удалось загрузить модель. Подробности: ~/Library/Application Support/Local Chat/model.log")
                    try:
                        with http("http://127.0.0.1:"+str(MODEL_PORT)+"/health",timeout=2) as r:
                            if r.status==200:break
                    except Exception:time.sleep(.5)
                else:raise RuntimeError("Загрузка заняла больше четырёх минут")
            with lock:
                if active is target:phase="ready"
        except Exception as e:
            with lock:
                if active is target:phase="error";failure=str(e)
    threading.Thread(target=ready,daemon=True).start()

def cleanup():
    try:stop()
    except Exception:pass
    if ollama_process and ollama_process.poll() is None:ollama_process.terminate()
atexit.register(cleanup)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def send(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(body)
    def valid(self):
        return self.headers.get("X-Local-Token")==TOKEN and self.headers.get("Host")==f"127.0.0.1:{PORT}"
    def do_GET(self):
        if self.path=="/":
            body=(ROOT/"index.html").read_text().replace("__TOKEN__",TOKEN).encode()
            self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store");self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'");self.end_headers();self.wfile.write(body);return
        if self.path.startswith("/vendor/"):
            name=self.path.removeprefix("/vendor/")
            if name not in ["marked.js","purify.js"]:return self.send({"error":"Not found"},404)
            body=(ROOT/"vendor"/name).read_bytes()
            self.send_response(200);self.send_header("Content-Type","text/javascript; charset=utf-8");self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","public, max-age=3600");self.end_headers();self.wfile.write(body);return
        if self.path=="/avatar.png":
            path=ROOT/"avatar.png";body=path.read_bytes()
            self.send_response(200);self.send_header("Content-Type","image/png");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
        if self.path.startswith("/api/image/"):
            query=parse_qs(urlparse(self.path).query)
            if query.get("token",[None])[0]!=TOKEN:return self.send({"error":"Forbidden"},403)
            try:
                a=caps.attachment(urlparse(self.path).path.rsplit("/",1)[-1]);body=caps.image_path(a).read_bytes()
                self.send_response(200);self.send_header("Content-Type","image/jpeg");self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","private, max-age=86400");self.end_headers();self.wfile.write(body)
            except Exception:self.send({"error":"Not found"},404)
            return
        if self.path=="/api/native/identity":
            if not caps.agent_tools.NATIVE_TOKEN or self.headers.get("X-Devine-Native")!=caps.agent_tools.NATIVE_TOKEN:return self.send({"error":"Forbidden"},403)
            return self.send({"ok":True,"protocol":3,"pid":os.getpid()})
        if self.path=="/api/native/pending":
            if not caps.agent_tools.NATIVE_TOKEN or self.headers.get("X-Devine-Native")!=caps.agent_tools.NATIVE_TOKEN:return self.send({"error":"Forbidden"},403)
            return self.send({"job":caps.agent_tools.native_pending()})
        if not self.valid():return self.send({"error":"Forbidden"},403)
        if self.path=="/api/browser/preview":
            return self.send(caps.agent_tools.browser_preview.snapshot())
        if self.path=="/api/agents":
            with db() as c:return self.send([dict(x) for x in c.execute("SELECT * FROM agent_runs ORDER BY updated DESC LIMIT 40")])
        if self.path=="/api/artifacts":return self.send(caps.agent_tools.artifacts())
        if self.path=="/api/tools":return self.send({"tools":[{"name":t["function"]["name"],"group":caps.agent_tools.group_for(t["function"]["name"]),"enabled":caps.agent_tools.permission(caps.agent_tools.group_for(t["function"]["name"]))} for t in caps.TOOLS],"native":caps.agent_tools.native_status})
        if self.path=="/api/project/tree":
            try:
                root=caps.agent_tools.checked(caps.settings().get("project") or '~')
                def walk(path,depth=0):
                    entries=[]
                    for e in sorted(path.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower())):
                        if e.name.startswith('.') or e.name in ['node_modules','__pycache__','venv','python-libs']:continue
                        if len(entries)>=100:break
                        if e.is_symlink():continue
                        item={"name":e.name,"path":str(e),"directory":e.is_dir()}
                        if e.is_dir() and depth<1:
                            try:item['children']=walk(e,depth+1)
                            except OSError:item['children']=[]
                        entries.append(item)
                    return entries
                return self.send({"root":str(root),"entries":walk(root)})
            except Exception as e:return self.send({"error":str(e)},400)
        if self.path=="/api/settings":return self.send(caps.settings())
        if self.path=="/api/access-log":return self.send(caps.history())
        if self.path=="/api/state":return self.send({"models":catalog,"active":active,"phase":phase,"error":failure,"external":external(),"generating":generating})
        with db() as c:
            if self.path=="/api/chats":return self.send([dict(x) for x in c.execute("SELECT * FROM chats ORDER BY updated DESC")])
            if self.path.startswith("/api/messages/"):
                messages=[]
                for x in c.execute("SELECT * FROM messages WHERE chat=? ORDER BY id",(self.path.rsplit("/",1)[-1],)):
                    m=dict(x);m["attachments"]=[caps.public_attachment(caps.attachment(a)) for a in json.loads(m.get("attachments") or "[]")];messages.append(m)
                return self.send(messages)
        self.send({"error":"Not found"},404)
    def do_POST(self):
        global catalog,generating
        native_request=self.path in ["/api/native/result","/api/native/stop"] and bool(caps.agent_tools.NATIVE_TOKEN) and self.headers.get("X-Devine-Native")==caps.agent_tools.NATIVE_TOKEN
        if not native_request and not self.valid():return self.send({"error":"Forbidden"},403)
        try:
            length=int(self.headers.get("Content-Length",0))
            if length>30_000_000:raise ValueError("Сообщение слишком большое")
            d=json.loads(self.rfile.read(length) or b"{}")
            if native_request:
                if self.path=="/api/native/stop":cancel.set()
                else:caps.agent_tools.native_result(d)
                return self.send({"ok":True})
            if self.path=="/api/project/file":
                path=caps.agent_tools.checked(d['path'])
                if not path.is_file() or path.stat().st_size>300000:raise ValueError('Выберите текстовый файл до 300 КБ')
                return self.send({"path":str(path),"text":path.read_text(),"sha256":caps.agent_tools.digest(path)})
            if self.path=="/api/project/save":
                path=caps.agent_tools.checked(d['path'])
                if caps.agent_tools.digest(path)!=d['sha256']:raise ValueError('Файл изменился на диске. Откройте его заново.')
                key=caps.agent_tools.backup(path);caps.agent_tools.atomic_write(path,d['text'].encode())
                return self.send({"sha256":caps.agent_tools.digest(path),"backup_id":key})
            if self.path=="/api/screen":
                result=caps.agent_tools.native_call('capture_screen',{},threading.Event())
                return self.send(caps.upload({"name":"Экран.jpg","data":result['data']}))
            if self.path=="/api/open-artifact":
                entry=next((a for a in caps.agent_tools.artifacts() if a['id']==d['id']),None)
                if not entry:raise ValueError('Файл не найден')
                path=Path(entry['path'])
                if not path.is_file():raise ValueError('Файл перемещён или удалён')
                subprocess.Popen(['/usr/bin/open',str(path)]);return self.send({"ok":True})
            if self.path=="/api/settings":return self.send(caps.save_settings(d))
            if self.path=="/api/upload":return self.send(caps.upload(d))
            if self.path=="/api/approve":caps.approve(d["id"],d.get("allow",False));return self.send({"ok":True})
            if self.path=="/api/start":start(d["model"]);return self.send({"ok":True})
            if self.path=="/api/stop":stop();return self.send({"ok":True})
            if self.path=="/api/stop-external":
                for pid in external():
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                return self.send({"ok":True})
            if self.path=="/api/refresh":catalog=models();return self.send({"ok":True})
            if self.path=="/api/folder":
                folder=str(Path(d["path"]).expanduser().resolve())
                if not Path(folder).is_dir():raise ValueError("Папка не найдена")
                f=DATA/"folders.json";folders=json.loads(f.read_text()) if f.exists() else []
                if folder not in folders:folders.append(folder)
                f.write_text(json.dumps(folders));catalog=models();return self.send({"ok":True})
            if self.path=="/api/cancel":
                cancel.set()
                caps.agent_tools.browser_preview.finish("Остановлено")
                if 'safari_browser' in sys.modules:threading.Thread(target=sys.modules['safari_browser'].browser.close,daemon=True).start()
                return self.send({"ok":True})
            if self.path=="/api/delete":
                with db() as c:c.execute("DELETE FROM messages WHERE chat=?",(d["id"],));c.execute("DELETE FROM chats WHERE id=?",(d["id"],))
                return self.send({"ok":True})
            if self.path=="/api/rename":
                with db() as c:c.execute("UPDATE chats SET title=? WHERE id=?",(d["title"][:100],d["id"]))
                return self.send({"ok":True})
            if self.path=="/api/chat":return self.chat(d)
            self.send({"error":"Not found"},404)
        except Exception as e:self.send({"error":str(e)},400)
    def chat(self,d):
        global generating
        with lock:
            if phase!="ready" or not active:raise ValueError("Сначала запустите модель")
            if generating:raise ValueError("Дождитесь текущего ответа")
            ids=d.get("attachments",[])
            if not isinstance(ids,list) or len(ids)>8:raise ValueError("Можно прикрепить до восьми файлов")
            text=d.get("text","").strip()
            if not text and not ids:raise ValueError("Введите сообщение или прикрепите файл")
            text=text or "Опиши содержимое вложенных файлов."
            model=active.copy()
            current_message=caps.enrich(text,ids,model)
            if len(json.dumps(current_message,ensure_ascii=False))>22_000_000:raise ValueError("Слишком много изображений. Отправьте их по отдельности.")
            generating=True;cancel.clear()
        cid=d.get("id") or uuid.uuid4().hex
        mode=d.get('mode','chat');role=d.get('agent','team')
        if mode not in ['chat','code']:mode='chat'
        if role not in ['team','planner','coder','reviewer']:role='team'
        caps.agent_tools.browser_preview.finish()
        caps.agent_tools.context(chat=cid,project=caps.settings().get('project',''),mode=mode)
        caps.agent_tools.agent_runner=lambda args,event,stop_event:self.delegate(model,args,event,stop_event,cid)
        content="";stream_started=False;error=None;all_reasoning="";thinking_ms=0
        def event(value):
            self.wfile.write((json.dumps(value,ensure_ascii=False)+"\n").encode());self.wfile.flush()
        try:
            with db() as c:
                c.execute("INSERT OR IGNORE INTO chats(id,title,model,updated,mode,agent,project) VALUES (?,?,?,?,?,?,?)",(cid,text[:60],model["name"],time.time(),mode,role,caps.settings().get("project","")))
                c.execute("INSERT INTO messages(chat,role,content,attachments) VALUES (?,?,?,?)",(cid,"user",text,json.dumps(ids)))
                c.execute("UPDATE chats SET updated=?,model=? WHERE id=?",(time.time(),model["name"],cid))
                rows=list(c.execute("SELECT role,content,attachments FROM messages WHERE chat=? ORDER BY id",(cid,)))
            msgs=[]
            for r in rows:
                if r["role"]=="user":msgs.append(caps.enrich(r["content"],json.loads(r["attachments"] or "[]"),model))
                else:msgs.append({"role":r["role"],"content":r["content"]})
            # Keep the newest turns within a conservative text budget; originals remain in history.
            while len(msgs)>1 and sum(len(json.dumps(m,ensure_ascii=False)) for m in msgs)>30000:
                msgs.pop(0)
                while msgs and msgs[0]["role"]!="user":msgs.pop(0)
            msgs.insert(0,caps.system_message(mode,role))
            self.send_response(200);self.send_header("Content-Type","application/x-ndjson");self.send_header("Connection","close");self.end_headers();self.close_connection=True;stream_started=True
            event({"id":cid})
            for turn in range(24):
                if cancel.is_set():break
                tools=caps.agent_tools.available() if turn<23 else []
                if model["engine"]=="Ollama":
                    url="http://127.0.0.1:11434/api/chat";payload={"model":model["name"],"messages":msgs,"stream":True,"keep_alive":-1}
                else:
                    url=f"http://127.0.0.1:{MODEL_PORT}/v1/chat/completions";payload={"messages":msgs,"stream":True,"max_tokens":4096,"temperature":.6,"chat_template_kwargs":{"enable_thinking":True},"reasoning_format":"deepseek","thinking_budget_tokens":768}
                speed=caps.settings().get('performance','balanced')
                if model['engine']!='Ollama':
                    payload['chat_template_kwargs']['enable_thinking']=speed!='fast'
                    payload['max_tokens']=2048 if speed=='fast' else 4096
                    payload['thinking_budget_tokens']=1536 if speed=='deep' else 768
                if tools:payload["tools"]=tools
                browser_task=bool(re.search(r'(открой|открыть|включи|включить|найди|найти|перейди|перейти|покажи|зайди|open|search|play)',text,re.I) and re.search(r'(браузер|сайт|ютуб|youtube|safari|сафари|https?://)',text,re.I))
                if re.search(r'не (?:надо |нужно |следует )?(?:откры|включ|заход|переход)',text,re.I):browser_task=False
                if turn==0 and browser_task and any(t['function']['name']=='browser_open' for t in tools):payload['tool_choice']={'type':'function','function':{'name':'browser_open'}}
                for old_message in msgs[1:-6]:
                    if old_message.get('role')=='tool' and len(old_message.get('content',''))>1800:old_message['content']=old_message['content'][:1600]+'\n[Результат предыдущего шага сокращён. При необходимости прочитайте файл заново.]'
                    if 'reasoning_content' in old_message:old_message['reasoning_content']=''
                calls={};part="";reasoning="";thought_started=None;thought_finished=None
                with http(url,payload,240) as r:
                    for raw in r:
                        if cancel.is_set():break
                        line=raw.decode().strip()
                        if not line:continue
                        if line.startswith("data: "):line=line[6:]
                        if line=="[DONE]":break
                        chunk=json.loads(line)
                        if "error" in chunk:raise RuntimeError(str(chunk["error"]))
                        if model["engine"]=="Ollama":delta=chunk.get("message",{});thought=delta.get("thinking","") or ""
                        else:delta=(chunk.get("choices") or [{}])[0].get("delta",{});thought=delta.get("reasoning_content","") or ""
                        token=delta.get("content","") or ""
                        if thought:
                            if thought_started is None:
                                thought_started=time.monotonic()
                                if all_reasoning:
                                    all_reasoning+="\n\n";event({"thinking":"\n\n"})
                            all_reasoning+=thought
                        if token and thought_started is not None and thought_finished is None:thought_finished=time.monotonic()
                        part+=token;content+=token;reasoning+=thought
                        if token or thought:event({"token":token,"thinking":thought})
                        for i,call in enumerate(delta.get("tool_calls",[])):
                            index=call.get("index",i);entry=calls.setdefault(index,{"id":"call_"+uuid.uuid4().hex[:12],"type":"function","function":{"name":"","arguments":""}})
                            if call.get("id"):entry["id"]=call["id"]
                            fn=call.get("function",{})
                            if fn.get("name"):entry["function"]["name"]+=fn["name"]
                            args=fn.get("arguments","")
                            entry["function"]["arguments"]+=json.dumps(args,ensure_ascii=False) if isinstance(args,dict) else args
                if thought_started is not None:
                    thinking_ms+=max(1,int(((thought_finished or time.monotonic())-thought_started)*1000))
                    event({"thinking_done":True,"thinking_ms":thinking_ms})
                if not calls or cancel.is_set():break
                tool_calls=list(calls.values())
                msgs.append({"role":"assistant","content":part,"reasoning_content":reasoning,"tool_calls":tool_calls})
                for call in tool_calls:
                    if cancel.is_set():break
                    try:args=json.loads(call["function"]["arguments"] or "{}")
                    except ValueError:args={}
                    result=caps.call_tool(call["function"]["name"],args,event,cancel)
                    msgs.append({"role":"tool","tool_call_id":call["id"],"name":call["function"]["name"],"content":json.dumps(result,ensure_ascii=False)})
                    if result.get('attachment') and result['attachment'].get('image'):
                        msgs.append(caps.enrich('Актуальный снимок экрана. Используй его размеры для координат.',[result['attachment']['id']],model))
        except (BrokenPipeError,ConnectionResetError):cancel.set()
        except Exception as e:
            error=str(e)
            if not stream_started:raise
        finally:
            caps.agent_tools.browser_preview.finish("Остановлено" if cancel.is_set() else "Завершено")
            if content or all_reasoning:
                with db() as c:c.execute("INSERT INTO messages(chat,role,content,reasoning,thinking_ms) VALUES (?,?,?,?,?)",(cid,"assistant",content,all_reasoning,thinking_ms))
            with lock:generating=False
        if stream_started:
            try:
                if error:event({"error":error})
                event({"done":True,"cancelled":cancel.is_set(),"thinking_ms":thinking_ms})
            except (BrokenPipeError,ConnectionResetError):pass

    def delegate(self,model,args,event,stop_event,cid):
        name=args.get('name','coder');task=str(args.get('task',''))
        if name not in ['planner','coder','reviewer'] or not task:raise ValueError('Укажите имя агента и задачу')
        rid=uuid.uuid4().hex
        with db() as c:c.execute('INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?)',(rid,cid,name,task,'running','',time.time()))
        event({'agent':{'id':rid,'name':name,'task':task,'status':'running','content':''}})
        messages=[caps.system_message('code',name),{'role':'user','content':task}];answer='';status='done'
        allowed={'list_directory','read_file','search_files','run_command'}
        if name=='coder':allowed|={'write_file','edit_file','make_directory','open_in_editor'}
        try:
            for _ in range(7):
                if stop_event.is_set():status='stopped';break
                tools=[t for t in caps.agent_tools.available() if t['function']['name'] in allowed]
                payload={'messages':messages,'stream':False,'max_tokens':4096,'temperature':.4,'chat_template_kwargs':{'enable_thinking':True},'reasoning_format':'deepseek','thinking_budget_tokens':512}
                if tools:payload['tools']=tools
                if model['engine']=='Ollama':
                    payload.update(model=model['name']);url='http://127.0.0.1:11434/api/chat'
                else:url=f'http://127.0.0.1:{MODEL_PORT}/v1/chat/completions'
                with http(url,payload,240) as response:data=json.load(response)
                msg=data.get('message',{}) if model['engine']=='Ollama' else data['choices'][0]['message']
                text=msg.get('content','') or '';answer+=text
                event({'agent':{'id':rid,'name':name,'task':task,'status':'running','content':answer}})
                calls=msg.get('tool_calls',[])
                if not calls:break
                normalized=[]
                for call in calls:
                    call.setdefault('id','agent_'+uuid.uuid4().hex[:10]);call.setdefault('type','function');normalized.append(call)
                messages.append({**msg,'tool_calls':normalized})
                for call in normalized:
                    fn=call['function'];arguments=fn.get('arguments',{})
                    if isinstance(arguments,str):arguments=json.loads(arguments or '{}')
                    result=caps.call_tool(fn['name'],arguments,event,stop_event) if fn['name'] in allowed else {'error':'Недоступно этой роли'}
                    messages.append({'role':'tool','name':fn['name'],'tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
            else:answer+='\nДостигнут лимит шагов агента.'
        except Exception as e:status='error';answer+='\n'+str(e)
        with db() as c:c.execute('UPDATE agent_runs SET status=?,content=?,updated=? WHERE id=?',(status,answer,time.time(),rid))
        event({'agent':{'id':rid,'name':name,'task':task,'status':status,'content':answer}})
        return {'agent':name,'status':status,'result':answer}

if __name__=="__main__":
    parent=os.getppid()
    if caps.agent_tools.NATIVE_TOKEN:
        def parent_watch():
            while True:
                time.sleep(1)
                if os.getppid()!=parent:os.kill(os.getpid(),signal.SIGTERM);return
        threading.Thread(target=parent_watch,daemon=True).start()
    server=ThreadingHTTPServer(("127.0.0.1",PORT),Handler)
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
    server.serve_forever()
