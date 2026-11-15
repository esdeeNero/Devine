import Cocoa
import WebKit
import ApplicationServices

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    let nativeToken=UUID().uuidString+UUID().uuidString
    var browserAgent:AgentBrowser?
    var nativeTimer: Timer?
    var nativeBusy=false
    var lastTarget:NSRunningApplication?
    var captureBounds=CGRect.zero
    var captureSize=CGSize.zero
    var captureTime:Date?
    var axNodes=[AXUIElement]()
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var port = 18743
    var attempts = 0
    var log: FileHandle?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.applicationIconImage=NSImage(contentsOf:Bundle.main.resourceURL!.appendingPathComponent("AppIcon.icns"))
        let menu = NSMenu()
        let item = NSMenuItem(); menu.addItem(item)
        let submenu = NSMenu(); submenu.addItem(withTitle: "О Devine", action: #selector(about), keyEquivalent: "")
        submenu.addItem(.separator()); submenu.addItem(withTitle: "Завершить Devine", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); item.submenu = submenu
        let edit = NSMenuItem(); menu.addItem(edit); edit.submenu = NSMenu(title: "Правка")
        for (title, action, key) in [("Отменить", "undo:", "z"),("Вырезать", "cut:", "x"),("Копировать", "copy:", "c"),("Вставить", "paste:", "v"),("Выбрать всё", "selectAll:", "a")] { edit.submenu?.addItem(withTitle: title, action: Selector(action), keyEquivalent: key) }
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x:0,y:0,width:1380,height:860),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title = "Devine"; window.minSize = NSSize(width:820,height:600); window.center()
        let config = WKWebViewConfiguration(); config.websiteDataStore = .default(); config.userContentController.add(self,name:"native")
        web = WKWebView(frame: window.contentView!.bounds, configuration: config)
        web.autoresizingMask = [.width,.height];web.navigationDelegate=self;web.uiDelegate=self
        window.contentView = web; window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
        web.loadHTMLString("<body style='background:#faf9f6;color:#647b53;font:18px -apple-system;display:grid;place-items:center;height:90vh'>✳ &nbsp; Devine запускается…</body>",baseURL:nil)
        startServer();startNativeBridge()
    }
    func startServer() {
        guard let resources = Bundle.main.resourceURL else { return }
        let p = Process(); p.executableURL = URL(fileURLWithPath:"/opt/homebrew/bin/python3")
        p.arguments = [resources.appendingPathComponent("server.py").path, String(port)]
        var env = ProcessInfo.processInfo.environment; env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"; env["DEVINE_NATIVE_TOKEN"]=nativeToken;env["PYTHONDONTWRITEBYTECODE"]="1"; p.environment = env
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Local Chat")
        try? FileManager.default.createDirectory(at:dir,withIntermediateDirectories:true)
        let logfile=dir.appendingPathComponent("app.log");FileManager.default.createFile(atPath:logfile.path,contents:nil)
        log=try? FileHandle(forWritingTo:logfile);p.standardOutput=log;p.standardError=log
        do { try p.run();server=p; waitForServer() } catch { showError(error.localizedDescription) }
    }
    func waitForServer() {
        attempts += 1
        if let p=server, !p.isRunning { showError("Не удалось запустить сервер. Проверьте ~/Library/Application Support/Local Chat/app.log");return }
        if attempts > 180 {showError("Сервер не отвечает. Перезапустите приложение.");return}
        let url=URL(string:"http://127.0.0.1:\(port)/")!
        var identity=URLRequest(url:URL(string:"http://127.0.0.1:\(port)/api/native/identity")!);identity.setValue(nativeToken,forHTTPHeaderField:"X-Devine-Native");identity.timeoutInterval=3
        URLSession.shared.dataTask(with:identity) { data,response,error in
            DispatchQueue.main.async {
                if error == nil, let http=response as? HTTPURLResponse, http.statusCode==200 { self.web.load(URLRequest(url:url,cachePolicy:.reloadIgnoringLocalCacheData)) }
                else { DispatchQueue.main.asyncAfter(deadline:.now()+0.5){self.waitForServer()} }
            }
        }.resume()
    }
    func showError(_ text:String) { let alert=NSAlert();alert.messageText="Devine";alert.informativeText=text;alert.runModal() }
    @objc func about() {showError("Локальные модели и личные разговоры.\nИстория: ~/Library/Application Support/Local Chat\n\nЗакрытие приложения выгружает запущенную в нём модель.")}
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy)->Void) {
        if let u=action.request.url, u.scheme=="about" || (u.host=="127.0.0.1" && u.port==port) {decisionHandler(.allow)} else {
            if action.navigationType == .linkActivated,let u=action.request.url,["http","https"].contains(u.scheme ?? "") {NSWorkspace.shared.open(u)}
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel=NSOpenPanel();panel.allowsMultipleSelection=parameters.allowsMultipleSelection;panel.canChooseDirectories=false;panel.canChooseFiles=true;panel.message="Выберите фото или файлы для локального ИИ"
        panel.beginSheetModal(for:window){response in completionHandler(response == .OK ? panel.urls : nil)}
    }
    func userContentController(_ userContentController:WKUserContentController,didReceive message:WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let url=message.frameInfo.request.url, url.host=="127.0.0.1",url.port==port,let data=message.body as? [String:Any],let action=data["action"] as? String else{return}
        if action=="showBrowser" {
            if browserAgent==nil {browserAgent=AgentBrowser(owner:self)}
            browserAgent?.window.makeKeyAndOrderFront(nil)
        } else if action=="permissions" {
            let permission=data["permission"] as? String ?? ""
            if permission=="screen" {CGRequestScreenCaptureAccess()}
            else if permission=="accessibility" {AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String:true] as CFDictionary)}
            let suffix=permission=="screen" ? "Privacy_ScreenCapture" : "Privacy_Accessibility"
            if let url=URL(string:"x-apple.systempreferences:com.apple.preference.security?"+suffix){NSWorkspace.shared.open(url)}
        } else if action=="appearance",let value=data["value"] as? String {
            NSApp.appearance = value=="system" ? nil : NSAppearance(named:value=="dark" ? .darkAqua : .aqua)
        } else if action=="avatar" {
            let id=data["id"] as? String ?? ""
            let base=FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Local Chat/attachments")
            if id.range(of:"^[a-f0-9]{32}$",options:.regularExpression) != nil,let img=NSImage(contentsOf:base.appendingPathComponent(id+"/image.jpg")){NSApp.applicationIconImage=img}
            else {NSApp.applicationIconImage=NSImage(contentsOf:Bundle.main.resourceURL!.appendingPathComponent("AppIcon.icns"))}
        } else if action=="folder" {
            let purpose=data["purpose"] as? String ?? "access"
            let panel=NSOpenPanel();panel.canChooseFiles=false;panel.canChooseDirectories=true;panel.allowsMultipleSelection=false;panel.prompt="Выбрать папку";panel.message=purpose=="access" ? "Разрешить Devine работу с файлами в выбранной папке" : purpose=="project" ? "Выберите рабочую папку проекта для Devine Code" : "Папка с локальными моделями"
            panel.beginSheetModal(for:window){response in
                guard response == .OK,let path=panel.url?.path else{return}
                if let json=try? JSONSerialization.data(withJSONObject:[path,purpose]),let encoded=String(data:json,encoding:.utf8) {self.web.evaluateJavaScript("window.nativeFolderPicked.apply(null,"+encoded+")",completionHandler:nil)}
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
    func applicationWillTerminate(_ notification:Notification) {
        nativeTimer?.invalidate();server?.terminate()
        if let p=server { p.waitUntilExit() }
        try? log?.close()
    }
}
let app=NSApplication.shared
let delegate=AppDelegate();app.delegate=delegate;app.run()
