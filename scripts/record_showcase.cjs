// Records the actual local browser UI and real API responses. No generated frames.
// Usage: node scripts/record_showcase.cjs [output-directory]
const fs=require('fs'),path=require('path'),{spawn}=require('child_process');
let pw;try{pw=require('playwright')}catch{pw=require('/Users/jerry/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')}
const out=path.resolve(process.argv[2]||'outputs/showcase');fs.mkdirSync(out,{recursive:true});
const ffmpeg=process.env.FFMPEG_BIN||'/private/tmp/look-video-tools/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1';
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const browser=await pw.chromium.launch({executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:false});
 const page=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
 let ff,interval,session;
 try{
  await page.goto('http://127.0.0.1:8765');await page.waitForSelector('#cards .card');
  ff=spawn(ffmpeg,['-y','-loglevel','error','-f','image2pipe','-framerate','10','-vcodec','mjpeg','-i','pipe:0','-an','-c:v','libx264','-preset','ultrafast','-crf','22','-pix_fmt','yuv420p','-movflags','+faststart',path.join(out,'look-live-browser.mp4')]);
  let ffError=null;ff.on('error',e=>ffError=e);ff.stderr.on('data',b=>process.stderr.write(b));
  let latest=await page.screenshot({type:'jpeg',quality:85});
  session=await page.context().newCDPSession(page);
  session.on('Page.screencastFrame',async e=>{latest=Buffer.from(e.data,'base64');await session.send('Page.screencastFrameAck',{sessionId:e.sessionId})});
  await session.send('Page.startScreencast',{format:'jpeg',quality:85,maxWidth:1440,maxHeight:1100,everyNthFrame:1});
  interval=setInterval(()=>{if(!ffError&&!ff.stdin.destroyed)ff.stdin.write(latest)},100);
  async function done(){await page.waitForFunction(async()=>!(await(await fetch('/api/state')).json()).busy,null,{timeout:140000});const s=await(await page.request.get('/api/state')).json();if(s.error)throw Error(s.error);return s}
  async function click(selector,action){await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/'+action)&&r.request().method()==='POST'),page.click(selector)])}
  await wait(4000);await click('#morning','rank');let s=await done();if(s.rows[0].label!=='mug')throw Error('Unexpected morning ranking');await wait(7000);
  await click('#afternoon','rank');s=await done();if(s.rows[0].label!=='water_bottle')throw Error('Unexpected afternoon ranking');await wait(7000);
  await click('[data-select="water_bottle"]','select');await done();await wait(4000);
  await click('#voice','voice');s=await done();if(s.arm!=='delivered')throw Error('Grok did not confirm; no fake success recorded');await wait(8000);
  // A fresh Factory run is opt-in because it may take 120s. Otherwise the UI says historical.
  if(process.env.RUN_FACTORY==='1'){await click('#factory','factory');await done();await wait(7000)}else{await wait(5000)}
  await page.screenshot({path:path.join(out,'look-live-browser.png'),fullPage:true});
  fs.writeFileSync(path.join(out,'evidence.json'),JSON.stringify(await(await page.request.get('/api/state')).json(),null,2));
  console.log('Recorded actual browser UI. Audio is not included. Factory historical unless RUN_FACTORY=1.');
 }finally{
  if(interval)clearInterval(interval);if(session)await session.send('Page.stopScreencast').catch(()=>{});
  if(ff){ff.stdin.end();await new Promise(r=>ff.on('close',r))}await browser.close();
 }
})().catch(e=>{console.error('Recording stopped:',e.message);process.exitCode=1});
