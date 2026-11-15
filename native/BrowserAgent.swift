import Cocoa
import WebKit

final class AgentPointerView:NSView {
    var point=CGPoint(x:90,y:100)
    override var isFlipped:Bool {true}
    override func hitTest(_ point:NSPoint)->NSView? {nil}
    override func draw(_ dirtyRect:NSRect){
        let p=NSBezierPath();p.move(to:point);p.line(to:CGPoint(x:point.x+3,y:point.y+24));p.line(to:CGPoint(x:point.x+9,y:point.y+18));p.line(to:CGPoint(x:point.x+14,y:point.y+28));p.line(to:CGPoint(x:point.x+19,y:point.y+25));p.line(to:CGPoint(x:point.x+14,y:point.y+15));p.line(to:CGPoint(x:point.x+23,y:point.y+14));p.close()
        NSColor(calibratedRed:0.70,green:0.54,blue:1,alpha:1).setFill();p.fill();NSColor.white.setStroke();p.lineWidth=1.5;p.stroke()
        let label=NSRect(x:point.x+24,y:point.y+25,width:69,height:25);NSColor(calibratedRed:0.35,green:0.23,blue:0.55,alpha:0.95).setFill();NSBezierPath(roundedRect:label,xRadius:8,yRadius:8).fill()
        ("Devine" as NSString).draw(at:CGPoint(x:label.minX+11,y:label.minY+5),withAttributes:[.font:NSFont.systemFont(ofSize:11,weight:.semibold),.foregroundColor:NSColor.white])
    }
    func move(_ destination:CGPoint) async {
        let origin=point
        for frame in 1...16 {let t=Double(frame)/16;let eased=1-pow(1-t,3);point=CGPoint(x:origin.x+(destination.x-origin.x)*eased,y:origin.y+(destination.y-origin.y)*eased);needsDisplay=true;try? await Task.sleep(nanoseconds:12_000_000)}
    }
}
final class AgentBrowser:NSObject,WKNavigationDelegate,WKUIDelegate,NSWindowDelegate,NSTextFieldDelegate {
    weak var owner:AppDelegate?
    var window:NSWindow!
    var web:WKWebView!
    var pointer:AgentPointerView!
    var address:NSTextField!
    var status:NSTextField!
    var addressEdited=false
    var halted=false
    var navigationError:String?
    var loading=false
    init(owner:AppDelegate){super.init();self.owner=owner
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:1160,height:800),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false);window.title="Браузер Devine";window.minSize=NSSize(width:760,height:520);window.center();window.delegate=self;window.isReleasedWhenClosed=false
        let root=NSView(frame:window.contentView!.bounds);root.autoresizingMask=[.width,.height];window.contentView=root
        let config=WKWebViewConfiguration();config.websiteDataStore = .nonPersistent();config.preferences.javaScriptCanOpenWindowsAutomatically=false;config.mediaTypesRequiringUserActionForPlayback=[]
        web=WKWebView(frame:NSRect(x:0,y:0,width:1160,height:746),configuration:config);web.autoresizingMask=[.width,.height];web.navigationDelegate=self;web.uiDelegate=self;root.addSubview(web)
        pointer=AgentPointerView(frame:web.bounds);pointer.autoresizingMask=[.width,.height];web.addSubview(pointer);pointer.isHidden=true
        let toolbar=NSView(frame:NSRect(x:0,y:746,width:1160,height:54));toolbar.autoresizingMask=[.width,.minYMargin];root.addSubview(toolbar)
        address=NSTextField(string:"");address.placeholderString="Адрес сайта · отдельная сессия";address.delegate=self;address.target=self;address.action=#selector(navigateManual);address.isBezeled=false;address.drawsBackground=false;address.frame=NSRect(x:18,y:25,width:880,height:20);address.autoresizingMask=[.width];address.font=NSFont.systemFont(ofSize:12,weight:.medium);toolbar.addSubview(address)
        status=NSTextField(labelWithString:"Готов к задаче");status.frame=NSRect(x:18,y:7,width:880,height:16);status.autoresizingMask=[.width];status.font=NSFont.systemFont(ofSize:10);status.textColor = .secondaryLabelColor;toolbar.addSubview(status)
        let stop=NSButton(title:"■ Остановить",target:self,action:#selector(stopAgent));stop.frame=NSRect(x:990,y:12,width:150,height:30);stop.autoresizingMask=[.minXMargin];stop.bezelStyle = .rounded;toolbar.addSubview(stop)
    }
    func controlTextDidChange(_ notification:Notification){if let field=notification.object as? NSTextField,field===address {addressEdited=true}}
    @objc func navigateManual(){guard addressEdited else{return};addressEdited=false;let text=address.stringValue.trimmingCharacters(in:.whitespacesAndNewlines);let raw=text.contains("://") ? text : "https://"+text;guard raw != web.url?.absoluteString else{return};if !text.isEmpty,let url=URL(string:raw),url.host != nil,["http","https"].contains(url.scheme ?? ""){stopAgent();web.load(URLRequest(url:url));status.stringValue="Вы управляете браузером"}}
    @objc func stopAgent(){halted=true;web.stopLoading();pointer.isHidden=true;status.stringValue="Остановлено · теперь управляете вы";owner?.stopBrowserTask()}
    func windowWillClose(_ notification:Notification){stopAgent()}
    func webView(_ webView:WKWebView,didStartProvisionalNavigation navigation:WKNavigation!){loading=true;navigationError=nil;address.stringValue=webView.url?.absoluteString ?? "Загрузка…"}
    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!){loading=false;address.stringValue=webView.url?.absoluteString ?? "";status.stringValue=halted ? "Вы управляете браузером" : "Страница загружена · Devine наблюдает"}
    func webView(_ webView:WKWebView,didFailProvisionalNavigation navigation:WKNavigation!,withError error:Error){loading=false;navigationError=error.localizedDescription;status.stringValue=error.localizedDescription}
    func webView(_ webView:WKWebView,didFail navigation:WKNavigation!,withError error:Error){loading=false;navigationError=error.localizedDescription}
    func webView(_ webView:WKWebView,decidePolicyFor action:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void){
        guard let u=action.request.url,["http","https","about"].contains(u.scheme?.lowercased() ?? "") else{decisionHandler(.cancel);return};decisionHandler(.allow)
    }
    func webView(_ webView:WKWebView,createWebViewWith configuration:WKWebViewConfiguration,for navigationAction:WKNavigationAction,windowFeatures:WKWindowFeatures)->WKWebView? {if let url=navigationAction.request.url,["http","https"].contains(url.scheme ?? ""){web.load(URLRequest(url:url))};return nil}
    func js(_ code:String) async throws -> Any? {try await web.evaluateJavaScript(code)}
    func settle() async {for _ in 0..<80 {if halted || !loading {break};try? await Task.sleep(nanoseconds:100_000_000)};try? await Task.sleep(nanoseconds:300_000_000)}
    func observe() async throws -> [String:Any] {
        let script="""
        (()=>{window.__devineNodes=[];const nodes=[];const selector='a[href],button,input,textarea,select,summary,[role="button"],[role="link"],[contenteditable="true"],video';
        for(const e of document.querySelectorAll(selector)){const r=e.getBoundingClientRect();const s=getComputedStyle(e);if(!r.width||!r.height||r.bottom<0||r.top>innerHeight||r.right<0||r.left>innerWidth||s.visibility==='hidden'||s.display==='none')continue;
        const id=window.__devineNodes.length;window.__devineNodes.push(e);nodes.push({id,tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',label:(e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||e.getAttribute('title')||'').slice(0,180),type:e.type||'',value:e.type==='password'?'[protected]':String(e.value||'').slice(0,160),href:e.href||'',x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});if(nodes.length>=140)break;}
        const media=[...document.querySelectorAll('video,audio')].map(v=>({paused:v.paused,ended:v.ended,time:v.currentTime,ready:v.readyState}));
        return {url:location.href,title:document.title,text:document.body?.innerText.slice(0,14000)||'',nodes,media,viewport:{width:innerWidth,height:innerHeight},loading:document.readyState!=='complete'};})()
        """
        var result=(try await js(script)) as? [String:Any] ?? [:];result["browser"]="Devine WebKit";if let error=navigationError {result["navigation_error"]=error};return result
    }
    func perform(_ action:String,_ args:[String:Any]) async -> [String:Any] {
        do {
            if action=="browser_open" {
                guard let raw=args["url"] as? String,let url=URL(string:raw),["http","https"].contains(url.scheme?.lowercased() ?? ""),url.host != nil,url.user==nil,url.password==nil else{return ["error":"Нужен адрес сайта http/https без пароля"]}
                halted=false;navigationError=nil;window.makeKeyAndOrderFront(nil);window.makeFirstResponder(web);pointer.isHidden=false;status.stringValue="Devine открывает сайт…";loading=true;web.load(URLRequest(url:url));await settle();return try await observe()
            }
            guard !halted else{return ["error":"Браузер остановлен пользователем. Продолжение возможно только по новой просьбе пользователя."]}
            guard web.url != nil else{return ["error":"Сначала вызови browser_open"]}
            if action=="browser_observe"{await settle();return try await observe()}
            if action=="browser_snapshot" {
                let image=try await web.takeSnapshot(configuration:nil)
                guard let tiff=image.tiffRepresentation,let rep=NSBitmapImageRep(data:tiff),let jpeg=rep.representation(using:.jpeg,properties:[.compressionFactor:0.8]) else{return ["error":"Не удалось сделать снимок браузера"]}
                return ["data":jpeg.base64EncodedString(),"width":rep.pixelsWide,"height":rep.pixelsHigh]
            }
            if action=="browser_scroll"{let pixels=max(-1600,min(1600,args["pixels"] as? Int ?? 600));status.stringValue="Devine прокручивает страницу…";_ = try await js("window.scrollBy({top:\(pixels),behavior:'smooth'})");try? await Task.sleep(nanoseconds:500_000_000);return try await observe()}
            let encoded=String(data:try JSONSerialization.data(withJSONObject:args),encoding:.utf8)!
            let check="""
            (()=>{const a=\(encoded);const e=window.__devineNodes?.[a.id];if(!e||!e.isConnected)return {error:'Элемент устарел. Сначала browser_observe.'};const r=e.getBoundingClientRect();const label=(e.getAttribute('aria-label')||e.innerText||e.placeholder||'').slice(0,180);if(e.type==='password')return {error:'Пароль вводит пользователь вручную'};if(/купить|оплат|оформить заказ|удалить аккаунт|отправить сообщение|purchase|checkout|pay now|delete account|send message|captcha|sign in|log in|войти/i.test(label))return {error:'Требуется ручное действие пользователя: '+label};return {x:r.x+r.width/2,y:r.y+r.height/2,label};})()
            """
            let target=try await js(check) as? [String:Any] ?? [:];if target["error"] != nil{return target}
            let point=CGPoint(x:(target["x"] as? NSNumber)?.doubleValue ?? 70,y:(target["y"] as? NSNumber)?.doubleValue ?? 70)
            pointer.isHidden=false;await pointer.move(point);guard !halted else{return ["error":"Остановлено пользователем"]};window.orderFront(nil)
            if action=="browser_click"{
                status.stringValue="Devine нажимает: "+(target["label"] as? String ?? "элемент")
                _ = try await js("(()=>{const a=\(encoded);const e=window.__devineNodes?.[a.id];if(!e?.isConnected)throw Error('Элемент устарел');e.focus();e.click();})()")
            } else if action=="browser_type"{
                status.stringValue="Devine вводит текст…"
                _ = try await js("""
                (()=>{const a=\(encoded);const e=window.__devineNodes?.[a.id];if(!e?.isConnected)throw Error('Элемент устарел');if(e.type==='password')throw Error('Пароль вводит пользователь');e.focus();if(e.isContentEditable){e.textContent=a.text;}else{const proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;if(!setter)throw Error('Это не текстовое поле');setter.call(e,a.text);}e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));if(a.submit){const form=e.form;if(form)form.requestSubmit();else e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true}));}})()
                """)
            } else{return ["error":"Неизвестное действие браузера"]}
            try? await Task.sleep(nanoseconds:450_000_000);await settle();return try await observe()
        }catch{return ["error":error.localizedDescription]}
    }
}
extension AppDelegate {
    func stopBrowserTask(){
        var req=URLRequest(url:URL(string:"http://127.0.0.1:\(port)/api/native/stop")!);req.httpMethod="POST";req.setValue(nativeToken,forHTTPHeaderField:"X-Devine-Native");req.httpBody=Data("{}".utf8);URLSession.shared.dataTask(with:req).resume()
    }
}
