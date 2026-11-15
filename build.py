from pathlib import Path
import shutil,subprocess,sys
root=Path(__file__).resolve().parent
app=root/'build/Devine.app'
res=app/'Contents/Resources'
res.mkdir(parents=True,exist_ok=True)
(app/'Contents/MacOS').mkdir(exist_ok=True)
shutil.copytree(root/'resources',res,dirs_exist_ok=True)
shutil.copy2(root/'Info.plist',app/'Contents/Info.plist')
subprocess.run([sys.executable,'-m','pip','install','--target',str(res/'python-libs'),'-r',str(root/'requirements.txt')],check=True)
subprocess.run(['swiftc',str(root/'native/main.swift'),str(root/'native/NativeTools.swift'),str(root/'native/BrowserAgent.swift'),'-o',str(app/'Contents/MacOS/Devine'),'-framework','Cocoa','-framework','WebKit','-framework','ApplicationServices'],check=True)
subprocess.run(['swiftc',str(root/'native/DocumentText.swift'),'-o',str(res/'DocumentText'),'-framework','Cocoa','-framework','PDFKit'],check=True)
subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
print(app)
