import sys,unittest,threading
from pathlib import Path
sys.path.insert(0,str((Path(__file__).resolve().parents[1]/'resources')))
from safari_browser import SafariBrowser
class Fake(SafariBrowser):
 def __init__(self):super().__init__();self.calls=[]
 def start(self):self.session='owned-session'
 def request(self,method,path,payload=None,timeout=20):
  self.calls.append((method,path,payload))
  if path.endswith('/execute/sync'):
   script=payload['script']
   if script.startswith('const e='):return {'element-6066-11e4-a52e-4f735466cecf':'button-7'}
   if script.startswith('window.__devineSafariNodes'):return {'browser':'Safari','url':'https://example.com','nodes':[]}
   return None
  if path.endswith('/screenshot'):return 'aGVsbG8='
  return None
class SafariTests(unittest.TestCase):
 def test_navigation(self):
  b=Fake();r=b.perform('browser_open',{'url':'https://example.com'},threading.Event());self.assertEqual(r['browser'],'Safari');self.assertIn(('POST','/session/owned-session/url',{'url':'https://example.com'}),b.calls)
 def test_type_and_submit(self):
  b=Fake();b.start();b.perform('browser_type',{'id':2,'text':'Devine','submit':True},threading.Event());self.assertIn(('POST','/session/owned-session/element/button-7/value',{'text':'Devine\ue007'}),b.calls)
 def test_click_uses_native_webdriver(self):
  b=Fake();b.start();b.perform('browser_click',{'id':1},threading.Event());self.assertTrue(any(path.endswith('/element/button-7/click') for _,path,_ in b.calls))
 def test_cancel_prevents_action(self):
  b=Fake();event=threading.Event();event.set();self.assertIn('error',b.perform('browser_open',{'url':'https://example.com'},event));self.assertEqual(b.calls,[])
 def test_unsafe_navigation_rejected(self):
  b=Fake()
  for url in ['file:///etc/passwd','javascript:alert(1)','https://user:password@example.com']:
   with self.assertRaises(ValueError):b.perform('browser_open',{'url':url},threading.Event())
if __name__=='__main__':unittest.main(verbosity=2)
