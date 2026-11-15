import Cocoa
import WebKit
import ApplicationServices
import ScreenCaptureKit

extension AppDelegate {
    func startNativeBridge() {
        nativeTimer=Timer.scheduledTimer(withTimeInterval:0.65,repeats:true){[weak self] _ in self?.pollNative()}
        NSWorkspace.shared.notificationCenter.addObserver(forName:NSWorkspace.didActivateApplicationNotification,object:nil,queue:.main){[weak self] note in
            if let target=note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,target.processIdentifier != ProcessInfo.processInfo.processIdentifier,target.bundleIdentifier != "com.apple.systempreferences" {self?.lastTarget=target}
        }
    }
    func nativePermissions()->[String:Any] {return ["screen":CGPreflightScreenCaptureAccess(),"accessibility":AXIsProcessTrusted(),"connected":true]}
    func postNative(_ result:[String:Any],id:String="") {
        var request=URLRequest(url:URL(string:"http://127.0.0.1:\(port)/api/native/result")!);request.httpMethod="POST";request.setValue(nativeToken,forHTTPHeaderField:"X-Devine-Native");request.setValue("application/json",forHTTPHeaderField:"Content-Type")
        request.httpBody=try? JSONSerialization.data(withJSONObject:["id":id,"result":result,"permissions":nativePermissions()])
        URLSession.shared.dataTask(with:request).resume()
    }
    func pollNative() {
        if nativeBusy{return};nativeBusy=true
        var request=URLRequest(url:URL(string:"http://127.0.0.1:\(port)/api/native/pending")!);request.setValue(nativeToken,forHTTPHeaderField:"X-Devine-Native");request.timeoutInterval=4
        URLSession.shared.dataTask(with:request){[weak self] data,_,_ in
            DispatchQueue.main.async {
                guard let self=self else{return}
                guard let data=data,let value=try? JSONSerialization.jsonObject(with:data) as? [String:Any],let job=value["job"] as? [String:Any],let id=job["id"] as? String,let action=job["action"] as? String else {self.nativeBusy=false;self.postNative([:]);return}
                Task { @MainActor in
                    let result=await self.performNative(action,job["args"] as? [String:Any] ?? [:]);self.postNative(result,id:id);self.nativeBusy=false
                }
            }
        }.resume()
    }
    func focusTarget(){if let app=lastTarget,!app.isTerminated {app.activate(options:[])}}
    func performNative(_ action:String,_ args:[String:Any]) async -> [String:Any] {
        if action.hasPrefix("browser_") {
            if browserAgent==nil {browserAgent=AgentBrowser(owner:self)}
            return await browserAgent!.perform(action,args)
        }
        if action=="capture_screen" {
            guard CGPreflightScreenCaptureAccess() else{return ["error":"Разрешите Devine запись экрана в Системных настройках → Конфиденциальность и безопасность → Запись экрана."]}
            do {
                if lastTarget==nil || lastTarget!.isTerminated {
                    if let windows=CGWindowListCopyWindowInfo([.optionOnScreenOnly,.excludeDesktopElements],kCGNullWindowID) as? [[String:Any]],let window=windows.first(where:{($0[kCGWindowLayer as String] as? Int)==0 && ($0[kCGWindowOwnerPID as String] as? Int32) != ProcessInfo.processInfo.processIdentifier}),let pid=window[kCGWindowOwnerPID as String] as? Int32 {lastTarget=NSRunningApplication(processIdentifier:pid)}
                }
                focusTarget();try? await Task.sleep(nanoseconds:180_000_000)
                let content=try await SCShareableContent.excludingDesktopWindows(false,onScreenWindowsOnly:true)
                guard let display=content.displays.first(where:{$0.displayID==CGMainDisplayID()}) ?? content.displays.first else{return ["error":"Дисплей не найден"]}
                let own=content.applications.filter{$0.processID==ProcessInfo.processInfo.processIdentifier}
                let filter=SCContentFilter(display:display,excludingApplications:own,exceptingWindows:[])
                let config=SCStreamConfiguration();let ratio=min(1,1600.0/Double(display.width));config.width=Int(Double(display.width)*ratio);config.height=Int(Double(display.height)*ratio);config.showsCursor=true;config.captureResolution = .best
                let cg=try await SCScreenshotManager.captureImage(contentFilter:filter,configuration:config)
                let rep=NSBitmapImageRep(cgImage:cg)
                guard let jpg=rep.representation(using:.jpeg,properties:[.compressionFactor:0.82]) else{return ["error":"Не удалось подготовить снимок"]}
                captureBounds=CGDisplayBounds(display.displayID);captureSize=CGSize(width:cg.width,height:cg.height);captureTime=Date()
                return ["data":jpg.base64EncodedString(),"width":cg.width,"height":cg.height,"display":"main","coordinates":"pixels of this screenshot"]
            } catch{return ["error":error.localizedDescription]}
        }
        if action=="open_url" {
            guard let raw=args["url"] as? String,let url=URL(string:raw),["https","http"].contains(url.scheme?.lowercased() ?? ""),url.host != nil,url.user==nil,url.password==nil else{return ["error":"Допустим только адрес сайта http/https без логина и пароля"]}
            guard let safari=NSWorkspace.shared.urlForApplication(withBundleIdentifier:"com.apple.Safari") else{return ["error":"Safari не найден"]}
            do {
                let config=NSWorkspace.OpenConfiguration();config.activates=true
                let target=try await NSWorkspace.shared.open([url],withApplicationAt:safari,configuration:config);lastTarget=target
                return ["opened":url.absoluteString,"browser":"Safari","verified_page":false,"video_playback":"not_verified","next":"Inspect screen or accessibility tree to verify load and playback before claiming success"]
            }catch{return ["error":error.localizedDescription]}
        }
        if action=="activate_app" {
            let name=args["name"] as? String ?? ""
            if let running=NSWorkspace.shared.runningApplications.first(where:{$0.localizedName?.lowercased()==name.lowercased() || $0.bundleIdentifier==name}) {running.activate(options:[]);lastTarget=running;return ["activated":name]}
            let candidates=["/Applications/\(name).app","/System/Applications/\(name).app","/System/Applications/Utilities/\(name).app"]
            guard let path=candidates.first(where:{FileManager.default.fileExists(atPath:$0)}) else{return ["error":"Приложение не найдено: "+name]}
            do {let app=try await NSWorkspace.shared.openApplication(at:URL(fileURLWithPath:path),configuration:NSWorkspace.OpenConfiguration());lastTarget=app;return ["activated":name]}catch{return ["error":error.localizedDescription]}
        }
        guard AXIsProcessTrusted() else{return ["error":"Разрешите Devine универсальный доступ в Системных настройках → Конфиденциальность и безопасность → Универсальный доступ."]}
        if action=="inspect_screen" {
            let target=lastTarget ?? NSWorkspace.shared.frontmostApplication
            guard let target=target else{return ["error":"Нет активного приложения"]}
            let root=AXUIElementCreateApplication(target.processIdentifier);AXUIElementSetMessagingTimeout(root,0.4)
            var rows=[[String:Any]]();axNodes=[]
            func attr(_ node:AXUIElement,_ key:String)->CFTypeRef? {var value:CFTypeRef?;if AXUIElementCopyAttributeValue(node,key as CFString,&value) == .success{return value};return nil}
            func walk(_ node:AXUIElement,_ depth:Int){
                if depth>7 || rows.count>=260{return}
                let index=rows.count;let role=attr(node,kAXRoleAttribute) as? String ?? "";let title=attr(node,kAXTitleAttribute) as? String ?? attr(node,kAXDescriptionAttribute) as? String ?? ""
                let subrole=attr(node,kAXSubroleAttribute) as? String ?? ""
                let value=subrole=="AXSecureTextField" ? "[protected]" : String(describing:attr(node,kAXValueAttribute) ?? "" as CFString)
                var actions:CFArray?;AXUIElementCopyActionNames(node,&actions)
                rows.append(["id":index,"role":role,"title":String(title.prefix(200)),"value":String(value.prefix(240)),"depth":depth,"actions":actions as? [String] ?? []]);axNodes.append(node)
                if let children=attr(node,kAXChildrenAttribute) as? [AXUIElement] {for child in children {walk(child,depth+1);if rows.count>=260{break}}}
            }
            walk(root,0);return ["app":target.localizedName ?? "","nodes":rows]
        }
        focusTarget();try? await Task.sleep(nanoseconds:120_000_000)
        if action=="computer_press" {
            let index=args["id"] as? Int ?? -1;guard index>=0,index<axNodes.count else{return ["error":"Элемент устарел. Выполните inspect_screen заново."]}
            let result=AXUIElementPerformAction(axNodes[index],kAXPressAction as CFString)
            return result == .success ? ["pressed":index] : ["error":"Элемент не поддерживает нажатие"]
        }
        if action=="computer_click" {
            guard let date=captureTime,Date().timeIntervalSince(date)<120,captureSize.width>0 else{return ["error":"Сначала получите свежий снимок экрана"]}
            let x=(args["x"] as? NSNumber)?.doubleValue ?? -1;let y=(args["y"] as? NSNumber)?.doubleValue ?? -1
            guard x>=0,y>=0,x<captureSize.width,y<captureSize.height else{return ["error":"Координаты вне снимка"]}
            let point=CGPoint(x:captureBounds.minX+x*captureBounds.width/captureSize.width,y:captureBounds.minY+y*captureBounds.height/captureSize.height)
            let right=args["button"] as? String=="right";let count=(args["double"] as? Bool ?? false) ? 2 : 1
            for i in 1...count {
                let down=CGEvent(mouseEventSource:nil,mouseType:right ? .rightMouseDown : .leftMouseDown,mouseCursorPosition:point,mouseButton:right ? .right : .left);let up=CGEvent(mouseEventSource:nil,mouseType:right ? .rightMouseUp : .leftMouseUp,mouseCursorPosition:point,mouseButton:right ? .right : .left)
                down?.setIntegerValueField(.mouseEventClickState,value:Int64(i));up?.setIntegerValueField(.mouseEventClickState,value:Int64(i));down?.post(tap:.cghidEventTap);up?.post(tap:.cghidEventTap)
            }
            return ["clicked":true,"x":x,"y":y]
        }
        if action=="computer_type" {
            let text=String((args["text"] as? String ?? "").prefix(10000))
            for char in text {
                let units=Array(String(char).utf16);let e=CGEvent(keyboardEventSource:nil,virtualKey:0,keyDown:true)
                units.withUnsafeBufferPointer {buffer in if let base=buffer.baseAddress {e?.keyboardSetUnicodeString(stringLength:buffer.count,unicodeString:base)}}
                e?.post(tap:.cghidEventTap)
            }
            return ["typed_characters":text.count]
        }
        if action=="computer_key" {
            let combination=(args["key"] as? String ?? "").lowercased().split(separator:"+").map(String.init);guard let key=combination.last else{return ["error":"Укажите клавишу"]}
            let keys:[String:CGKeyCode]=["a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,"w":13,"e":14,"r":15,"y":16,"t":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"9":25,"7":26,"8":28,"0":29,"o":31,"u":32,"i":34,"p":35,"return":36,"enter":36,"l":37,"j":38,"k":40,"n":45,"m":46,"tab":48,"space":49,"backspace":51,"escape":53,"left":123,"right":124,"down":125,"up":126]
            guard let code=keys[key] else{return ["error":"Неподдерживаемая клавиша"]};var flags:CGEventFlags=[]
            for modifier in combination.dropLast(){switch modifier {case "cmd","command","meta":flags.insert(.maskCommand);case "shift":flags.insert(.maskShift);case "ctrl","control":flags.insert(.maskControl);case "alt","option":flags.insert(.maskAlternate);default:return ["error":"Неизвестный модификатор"]}}
            let down=CGEvent(keyboardEventSource:nil,virtualKey:code,keyDown:true);let up=CGEvent(keyboardEventSource:nil,virtualKey:code,keyDown:false);down?.flags=flags;up?.flags=flags;down?.post(tap:.cghidEventTap);up?.post(tap:.cghidEventTap);return ["key":combination.joined(separator:"+")]
        }
        if action=="computer_scroll" {let n=(args["pixels"] as? Int ?? -500);CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:Int32(clamping:n),wheel2:0,wheel3:0)?.post(tap:.cghidEventTap);return ["scrolled":n]}
        return ["error":"Неизвестная функция macOS"]
    }
}
