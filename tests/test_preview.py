import sys,io,base64,threading,time,unittest
from pathlib import Path
sys.path.insert(0,str((Path(__file__).resolve().parents[1]/'resources')))
from browser_preview import BrowserPreview
from PIL import Image
class PreviewTests(unittest.TestCase):
 def test_frames_are_bounded_and_stop_at_completion(self):
  raw=io.BytesIO();Image.new('RGB',(2400,1600),'purple').save(raw,format='PNG')
  p=BrowserPreview();cancel=threading.Event()
  p.begin('safari',lambda:{'data':base64.b64encode(raw.getvalue()).decode()},lambda:True,cancel)
  p.observed({'title':'Fixture','url':'https://example.com/'})
  deadline=time.monotonic()+4
  while not p.snapshot()['revision'] and time.monotonic()<deadline:time.sleep(.05)
  s=p.snapshot();self.assertGreater(s['revision'],0)
  im=Image.open(io.BytesIO(base64.b64decode(s['image'].split(',')[1])));self.assertEqual(im.size,(960,640))
  p.finish();time.sleep(1.4);self.assertEqual(p.snapshot()['revision'],s['revision']);self.assertFalse(p.snapshot()['active'])
  p.begin('safari',lambda:None,lambda:True,cancel);self.assertIsNone(p.snapshot()['image']);p.finish()
 def test_revoked_access_stops_capture(self):
  p=BrowserPreview();calls=[]
  p.begin('safari',lambda:calls.append(1),lambda:False,threading.Event());p.observed({})
  time.sleep(1.4);self.assertEqual(calls,[]);self.assertFalse(p.snapshot()['active'])
unittest.main()
