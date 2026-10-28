/* Supplemental footage on the unchanged checkpoint-2 source commit 953b71b.
   Preparation and reproduction: docs/checkpoint-2-270.md.
   No response interception, simulated application screens or ORM mutations. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const preparedBrowsers=path.join(__dirname,'..','.local','checkpoint2-tools','browsers');
if(!process.env.PLAYWRIGHT_BROWSERS_PATH && fs.existsSync(path.join(preparedBrowsers,'ffmpeg-1011')))process.env.PLAYWRIGHT_BROWSERS_PATH=preparedBrowsers;
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(__dirname, '..', '.local', 'checkpoint2-tools', 'node_modules', 'playwright'));

const root = path.resolve(__dirname, '..');
const fixture = path.join(root, '.local', 'checkpoint2-270');
const output = process.env.VIDEO_TOUR_OUTPUT || path.join(fixture, 'recording');
const base = (process.env.VIDEO_BASE_URL || 'http://127.0.0.1:8024').replace(/\/$/, '');
const scale = Number(process.env.VIDEO_TOUR_HOLD_SCALE || '0.25');
const recording = process.env.VIDEO_TOUR_RECORD !== '0';
const only = process.env.VIDEO_TOUR_ONLY || '';
assert(!only || (!recording && only === 'roles'), 'A published recording must include the complete walkthrough.');
const python = process.env.VIDEO_TOUR_PYTHON || (process.platform==='win32'?'python':'python3');
const manifest = JSON.parse(fs.readFileSync(path.join(fixture, 'manifest.json'), 'utf8'));
const credentials = JSON.parse(fs.readFileSync(path.join(fixture, 'credentials.json'), 'utf8'));
const password = process.env.VIDEO_TOUR_PASSWORD || credentials.password;
const ids = manifest.ids;
const sourceCommit='953b71b3f647cd47d03dae2e12e758b9a0c6d09f';
const plan=JSON.parse(fs.readFileSync(path.join(fixture,'plan.json'),'utf8'));
assert.equal(manifest.source_commit,sourceCommit);
assert.equal(execFileSync('git',['rev-parse','HEAD'],{cwd:manifest.source_worktree,encoding:'utf8'}).trim(),sourceCommit);
assert.equal(execFileSync('git',['status','--porcelain','--untracked-files=no'],{cwd:manifest.source_worktree,encoding:'utf8'}).trim(),'');
const narrationIndexPath = path.join(fixture, 'narration-index.json');
const narrationIndex = fs.existsSync(narrationIndexPath) ? JSON.parse(fs.readFileSync(narrationIndexPath, 'utf8')) : {};
assert(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Use an isolated local server.');
assert.equal(new URL(base).origin,new URL(manifest.base_url).origin,'Server origin must match the prepared isolated video fixture.');
assert(Number.isFinite(scale) && scale >= 0 && scale <= 3, 'Invalid hold scale.');
assert(password && ids, 'Prepared isolated data and private credentials are required.');
fs.mkdirSync(output, {recursive: true});
const artifacts = path.join(output, 'artifacts');
fs.mkdirSync(artifacts, {recursive: true});
const escapeHtml = text => String(text).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
let runningBrowser, runningServer;

(async () => {
  const server = http.createServer((req, res) => {
    const file = path.resolve(artifacts, '.' + decodeURIComponent(new URL(req.url, 'http://localhost').pathname));
    if (!file.startsWith(artifacts + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end('Not found'); return;
    }
    const extension = path.extname(file);
    res.setHeader('Content-Type', extension === '.png' ? 'image/png' : extension === '.html' ? 'text/html; charset=utf-8' : 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  runningServer=server;
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const viewerBase = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', slowMo: recording ? 75 : 0});
  runningBrowser=browser;
  const context = await browser.newContext({
    viewport: {width: 1440, height: 960}, deviceScaleFactor: 1,
    locale: 'ru-RU', timezoneId: 'Asia/Yekaterinburg', acceptDownloads: true,
    ...(recording ? {recordVideo: {dir: path.join(output, 'raw'), size: {width:1440,height:960}}} : {}),
  });
  await context.addInitScript(() => {
    window.tourCaption = function (value) {
      if (value) sessionStorage.setItem('video-tour-caption', JSON.stringify(value));
      const saved = sessionStorage.getItem('video-tour-caption');
      if (!saved || !document.body) return;
      let box = document.getElementById('video-tour-caption');
      if (!box) {
        box = document.createElement('aside');
        box.id = 'video-tour-caption';
        box.setAttribute('aria-label','Титры видеозаписи');
        box.style.cssText = 'position:fixed;left:274px;right:24px;bottom:14px;z-index:2147483647;background:#122d2ef5;color:white;border-left:5px solid #d6ff61;border-radius:10px;padding:13px 18px;box-shadow:0 4px 24px #0004;pointer-events:none;font:17px/1.35 Arial,sans-serif';
        document.body.append(box);
      }
      const data=JSON.parse(saved);
      box.replaceChildren();
      const heading=document.createElement('strong');
      heading.textContent=data.heading;
      heading.style.cssText='display:block;color:#d6ff61;font-size:15px;margin-bottom:4px';
      const text=document.createElement('span'); text.textContent=data.text;
      box.append(heading,text);
    };
    document.addEventListener('DOMContentLoaded', () => {
      window.tourCaption();
      const marker=document.createElement('div');
      marker.id='video-tour-data-marker';
      marker.textContent='ЗАПИСЬ НА ТЕСТОВЫХ ДАННЫХ';
      marker.style.cssText='position:fixed;right:12px;top:5px;color:#42605f;background:#ffffffdf;font:10px Arial,sans-serif;padding:3px 7px;border-radius:4px;z-index:2147483646;pointer-events:none';
      document.body.append(marker);
    });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const video = page.video();
  const started = Date.now();
  const chapters=[], downloads=[], pageErrors=[], navigationErrors=[], narrationEvents=[];
  let captionValue={heading:'ОренКод · полный обзор',text:'Реальная работа сайта. Все участники, результаты и организации в записи вымышлены.'};
  let currentRole=null;
  let successful=false;
  page.on('pageerror', error=>pageErrors.push(error.message));
  page.on('response',response=>{
    if (response.request().isNavigationRequest() && response.url().startsWith(base) && response.status()>=500)
      navigationErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });

  async function hold(seconds=3){if(scale) await page.waitForTimeout(seconds*scale*1000);}
  async function renderCaption(){await page.evaluate(value=>window.tourCaption(value),captionValue);}
  async function speakHold(text,kind,seconds){
    narrationEvents.push({seconds:Number(((Date.now()-started)/1000).toFixed(3)),text,kind});
    if(process.env.VIDEO_TOUR_REQUIRE_NARRATION==='1')assert(narrationIndex[text]?.duration>0 && fs.existsSync(narrationIndex[text].file),'Prepare all narration before recording: '+text);
    await hold(Math.max(seconds,Number(narrationIndex[text]?.duration||0)+.45));
  }
  async function chapter(code,title,text){
    captionValue={heading:`${code} · ${title}`,text};
    chapters.push({seconds:Number(((Date.now()-started)/1000).toFixed(2)),code,title,text});
    console.log('Chapter:',code,title);
    await renderCaption();await speakHold(`${title}. ${text}`,'chapter',2);
  }
  async function say(text,seconds=3){captionValue={...captionValue,text};await renderCaption();await speakHold(text,'caption',seconds);}
  async function goto(route){
    const response=await page.goto(base+route,{waitUntil:'networkidle'});
    assert(response && response.status()===200,`Cannot open ${route}: HTTP ${response?.status()}`);
    await renderCaption();
  }
  async function focus(locator){
    await locator.first().scrollIntoViewIfNeeded();
    await locator.first().evaluate(el=>el.scrollIntoView({block:'center',behavior:'instant'}));
    await locator.first().hover();
    if(recording) await page.waitForTimeout(250);
  }
  async function click(locator){
    await focus(locator);
    await locator.click();
    await page.waitForLoadState('networkidle');
    await renderCaption();
    assert.equal(await page.locator('.errorlist,.message.error').count(),0,'The application rejected a form or action.');
  }
  async function login(role){
    if(currentRole===role && !page.url().startsWith(viewerBase) && await page.locator('.role-strip').count() && await page.getByRole('button',{name:'Выйти',exact:true}).count())return;
    if(page.url().startsWith(viewerBase))await goto('/');
    const logout=page.getByRole('button',{name:'Выйти',exact:true});
    if(await logout.count())await click(logout);
    else await goto('/login/');
    assert.equal(new URL(page.url()).pathname,'/login/','Logout did not reach login.');
    await page.locator('[name=username]').fill(manifest.users[role].username);
    await page.locator('[name=password]').fill(password);
    await hold(.7);
    await click(page.locator('button[type=submit]').first());
    assert.equal(new URL(page.url()).pathname,role==='ministry'?'/governance/ministry/':'/','Test account requires activation or login failed.');
    currentRole=role;
    const roleLabels={federation:'Федерация',athlete:'Спортсмен',coach:'Тренер',judge:'Судья',ministry:'Министерство'};
    assert.match(await page.locator('.role-strip').innerText(),new RegExp(roleLabels[role]));
    await hold(1);
  }
  async function download(locator,filename){
    await focus(locator);
    const promise=page.waitForEvent('download');
    await locator.click();
    const item=await promise;
    assert.equal(await item.failure(),null,'File download failed.');
    const target=path.join(artifacts,path.basename(filename));
    await item.saveAs(target);
    const bytes=fs.readFileSync(target);
    assert(bytes.length>100,'Empty downloaded file.');
    const extension=path.extname(target);
    if(extension==='.pdf')assert.equal(bytes.subarray(0,5).toString(),'%PDF-');
    if(['.xlsx','.docx'].includes(extension))assert.equal(bytes.subarray(0,2).toString(),'PK');
    downloads.push({file:path.basename(target),bytes:bytes.length,source:await item.url()});
    console.log('Downloaded:',path.basename(target),bytes.length,'bytes');
    return target;
  }
  async function viewFile(target,title){
    const filename=path.basename(target), extension=path.extname(target);
    let body='';
    if(extension==='.pdf'){
      const prefix=path.join(artifacts,filename+'.preview');
      execFileSync('pdftoppm',['-f','1','-singlefile','-scale-to','1700','-png',target,prefix],{stdio:'pipe'});
      body=`<img class="pdf" src="/${encodeURIComponent(path.basename(prefix+'.png'))}" alt="Первая страница скачанного PDF">`;
    }else if(extension==='.xlsx'){
      const data=JSON.parse(execFileSync(python,['-c',
        "import json,sys;from openpyxl import load_workbook;w=load_workbook(sys.argv[1],data_only=True);print(json.dumps([{'name':s.title,'rows':[[str(v) if v is not None else '' for v in row] for row in s.iter_rows(max_row=min(s.max_row,40),max_col=min(s.max_column,12),values_only=True)]} for s in w.worksheets],ensure_ascii=False))",target],{encoding:'utf8'}));
      assert(data.length && data.some(sheet=>sheet.rows.length),'Workbook has no data.');
      body=data.map(sheet=>`<section><h2>Лист: ${escapeHtml(sheet.name)}</h2><table>${sheet.rows.map(row=>`<tr>${row.map(value=>`<td>${escapeHtml(value)}</td>`).join('')}</tr>`).join('')}</table></section>`).join('');
    }else throw new Error('Unsupported document viewer: '+extension);
    const html=`<!doctype html><html lang="ru"><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>body{margin:0;background:#edf2f3;color:#183238;font:17px/1.4 Arial,sans-serif}header{background:#173b40;color:#fff;padding:18px 32px}header h1{font-size:24px;margin:0 0 5px}header p{font-size:14px;opacity:.9;margin:0}main{padding:20px 30px 135px}table{border-collapse:collapse;background:white;width:100%;font-size:16px}td{border:1px solid #cfdbdf;padding:10px 13px}tr:first-child{background:#deebef;font-weight:bold}h2{font-size:18px}.pdf{display:block;max-width:100%;max-height:700px;margin:0 auto;box-shadow:0 3px 20px #0002}</style><header><h1>${escapeHtml(title)}</h1><p>Просмотр фактически скачанного файла: ${escapeHtml(filename)} · содержимое получено из приложения</p></header><main>${body}</main></html>`;
    const viewer=filename+'.html';
    fs.writeFileSync(path.join(artifacts,viewer),html);
    await page.goto(`${viewerBase}/${encodeURIComponent(viewer)}`,{waitUntil:'networkidle'});
    await renderCaption();
    await hold(6);
  }
  const ctx={page,base,output,manifest,ids,goto,login,chapter,say,hold,focus,click,download,viewFile,assert};

  const scenes=[];
  async function scene(id,fn){
    const spec=plan.scenes.find(item=>item.id===id);
    assert(spec,'Missing planned scene: '+id);
    const start=Number(((Date.now()-started)/1000).toFixed(3));
    await chapter(id,spec.title,spec.caption);
    await fn();await hold(4);
    const end=Number(((Date.now()-started)/1000).toFixed(3));
    scenes.push({id,start,end});
    console.log('Scene complete:',id,(end-start).toFixed(2),'seconds');
  }
  try{
    await goto('/login/');
    await scene('intro',async()=>{await login('federation');await goto('/');await hold(5);});
    await scene('rating',async()=>{
      const route=`/rating/?season=${manifest.year}&discipline=${ids.discipline}`;
      await goto(route);
      await click(page.locator(`a.table-name[href="/registry/${ids.athlete}/"]`));await hold(4);
      await goto(route);
      const members=page.locator('input[name=members]');
      assert((await members.count())>=2);
      await members.nth(0).check();await members.nth(1).check();await hold(3);
      await click(page.getByRole('button',{name:/Сформировать сборную/}));
      const team=page.locator('section.team-panel');await focus(team);
      assert.equal(await team.locator('.team-members .person-line').count(),2);
      await hold(6);
    });
    await scene('registry',async()=>{
      await goto('/registry/?kind=athlete');
      await page.getByRole('textbox',{name:'Поиск по реестру'}).fill('Александр');
      await click(page.getByRole('button',{name:'Применить',exact:true}));
      await click(page.locator(`a.table-name[href="/registry/${ids.athlete}/"]`));await hold(3);
      const athleteReport=await download(page.locator(`a[href="/documents/athlete/${ids.athlete}/pdf/"]`),'athlete-report.pdf');
      await viewFile(athleteReport,'Отчёт спортсмена · скачанный PDF');
      await goto(`/registry/${ids.athlete2}/edit/`);
      await focus(page.locator('input[name=roles]').first());
      await page.locator('input[name=roles][value=judge]').check();await hold(3);
      await click(page.getByRole('button',{name:/^Сохранить/}));
      await goto(`/reports/?year_from=${manifest.year-1}&year_to=${manifest.year}`);
      await focus(page.locator('.report-bars'));await hold(4);
      const workbook=await download(page.locator('a[href*="/documents/census/0/xlsx/"]'),'athlete-count.xlsx');
      await viewFile(workbook,'Численность за два года · скачанный XLSX');
    });
    await scene('ministry',async()=>{
      await login('ministry');await goto(`/governance/ministry/?year=${manifest.year}`);await hold(4);
      await focus(page.getByRole('heading',{name:'Календарь соревнований',exact:true}));await hold(3);
      await focus(page.getByRole('heading',{name:'Составы сборной',exact:true}));await hold(3);
      await click(page.getByRole('link',{name:'Подробный отчёт',exact:true}));
      assert.equal(await page.locator('form[method=post] input[name=score],a[href$="/edit/"]').count(),0);
      await hold(4);
    });
    await scene('identity',async()=>{
      await login('federation');await goto('/identity/');await hold(3);
      await login('athlete');await goto('/identity/demo/');
      await click(page.getByRole('button',{name:'Войти через ФСП ID',exact:true}));await hold(4);
      await click(page.getByRole('button',{name:'Разрешить передачу данных',exact:true}));
      await focus(page.getByRole('heading',{name:'Проверенный ID token',exact:true}));await hold(4);
      assert((await page.locator('body').innerText()).includes('userinfo'));
      await login('federation');await goto('/integrations/');
      const form=page.locator('form').filter({has:page.locator('input[name=action][value=import]')});
      await form.locator('[name=competition]').selectOption(String(ids.import_competition));
      await form.locator('[name=file]').setInputFiles(manifest.files.results_csv);await focus(form);await hold(4);
      await click(form.getByRole('button',{name:'Проверить и импортировать',exact:true}));
      assert((await page.locator('body').innerText()).includes('Импортировано результатов:'));
      await goto(`/competitions/${ids.import_competition}/`);await focus(page.locator('#results'));await hold(5);
    });
    await scene('training',async()=>{
      await login('coach');await goto('/coaching/');await hold(3);
      await goto(`/training/groups/${ids.group}/`);await hold(3);
      await click(page.getByRole('link',{name:'Добавить занятие',exact:true}));
      await page.locator('#id_date').fill(manifest.today);
      await page.locator('#id_topic').fill('Алгоритмы и командная работа · чекпоинт 2');
      await page.locator('#id_format').selectOption('offline');
      await click(page.locator('form.form-stack').getByRole('button',{name:/^Сохранить/}));
      await page.locator(`input[name=present][value="${ids.athlete}"]`).check();
      await click(page.getByRole('button',{name:'Сохранить посещаемость',exact:true}));
      await focus(page.getByRole('heading',{name:'Зафиксированные отметки',exact:true}));await hold(4);
      await goto('/training/norms/');await hold(3);
      await goto(`/training/groups/${ids.group}/assessments/new/`);
      await page.locator('#id_athlete').selectOption(String(ids.athlete));
      await page.locator('#id_norm').selectOption(String(ids.norm));
      await page.locator('#id_value').fill('250');await page.locator('#id_tested_on').fill(manifest.today);
      await click(page.locator('form.form-stack').getByRole('button',{name:/^Сохранить/}));
      await focus(page.getByRole('heading',{name:'Сдача нормативов',exact:true}));await hold(4);
      await goto(`/training/enrollments/${ids.enrollment}/transfer/`);await hold(4);
    });
    await scene('appeal',async()=>{
      await login('athlete');await goto('/governance/appeals/');
      await page.locator('[name=result]').selectOption(String(ids.appealable_result));
      await page.locator('textarea[name=reason]').fill('Прошу проверить начисление баллов по третьей задаче. Тестовое обращение в записи версии второго чекпоинта.');
      await hold(3);await click(page.getByRole('button',{name:'Отправить апелляцию',exact:true}));
      const route=new URL(page.url()).pathname;assert.match(route,/\/appeals\/\d+\/$/);await hold(3);
      await login('federation');await goto(route);
      await page.locator('[name=status]').selectOption('reviewed');
      await page.locator('textarea[name=reason]').fill('Обращение принято к рассмотрению. Проводится проверка журнала результатов.');
      await click(page.getByRole('button',{name:'Сохранить решение',exact:true}));
      assert((await page.locator('body').innerText()).includes('На рассмотрении'));await hold(5);
    });
    await scene('development',async()=>{
      await goto(`/development/?year=${manifest.year}`);await focus(page.locator('.stats-grid'));await hold(3);
      await page.locator('form.filter-bar input[name=year]').fill(String(manifest.year-1));
      await click(page.getByRole('button',{name:'Показать',exact:true}));await hold(3);
      await goto(`/development/pathway/?athlete=${ids.athlete}`);
      await focus(page.getByRole('heading',{name:'Понятия простым языком',exact:true}));await hold(4);
      await goto(`/governance/awards/?year=${manifest.year}`);
      await focus(page.getByRole('heading',{name:'Автоматически найденные кандидаты',exact:true}));await hold(4);
      await click(page.getByRole('button',{name:'Подготовить',exact:true}).first());
      await focus(page.getByRole('heading',{name:'Основание и документы',exact:true}));await hold(4);
    });
    await scene('qr',async()=>{
      await login('athlete');await goto('/development/certificates/');
      await page.locator('[name=result]').selectOption(String(ids.result));
      await page.locator('[name=public_consent]').check();await hold(2);
      const file=await download(page.getByRole('button',{name:'Сформировать PDF',exact:true}),'participation-qr.pdf');
      await viewFile(file,'Справка об участии · фактически скачанный PDF с QR');
      await goto('/development/certificates/');
      const card=page.locator('article.qualification-card').first();
      const href=await card.getByRole('link',{name:'Публичная проверка',exact:true}).getAttribute('href');
      await click(page.getByRole('button',{name:'Выйти',exact:true}));
      await goto(new URL(href,base).pathname);
      assert((await page.locator('body').innerText()).includes('Действительна'));await hold(5);
    });
    await scene('guidejudge',async()=>{
      await goto('/guide/');await focus(page.getByRole('heading',{name:'Четыре шага до первого результата',exact:true}));await hold(4);
      await login('judge');await goto('/qualifications/');await hold(3);
      const form=page.locator('form').filter({has:page.getByRole('button',{name:'Отправить на рассмотрение',exact:true})});
      await form.locator('[name=kind]').selectOption('category');
      await form.locator('[name=athlete]').selectOption(String(ids.judge));
      await form.locator('[name=requested_title]').selectOption({label:'Судья второй категории'});
      await click(form.getByRole('button',{name:'Отправить на рассмотрение',exact:true}));
      await goto('/development/judging/');await hold(3);
      const report=await download(page.locator(`a[href="/development/judging/history/${ids.judge}/pdf/"]`),'judging-history.pdf');
      await viewFile(report,'Личная история судейства · скачанный PDF');
    });
    await scene('outro',async()=>{await goto('/');await login('federation');await goto('/');await hold(5);});
    assert.deepEqual(pageErrors,[]);assert.deepEqual(navigationErrors,[]);
    successful=true;
  }catch(error){
    await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});
    fs.writeFileSync(path.join(output,'failure.txt'),`${error.stack}\nURL:${page.url()}\n`);
    throw error;
  }finally{
    fs.writeFileSync(path.join(output,'evidence.json'),JSON.stringify({successful,sourceCommit,scenes,errors:[...pageErrors,...navigationErrors],downloads,durationSeconds:(Date.now()-started)/1000,sourceWorktreeClean:execFileSync('git',['status','--porcelain','--untracked-files=no'],{cwd:manifest.source_worktree,encoding:'utf8'}).trim()===''},null,2));
    await context.close();
    if(video)await video.saveAs(path.join(output,successful?'raw-tour.webm':'incomplete.webm'));
    await browser.close();await new Promise(resolve=>server.close(resolve));
  }
  console.log('Historical supplemental recording complete:',scenes.length,'scenes;',downloads.length,'downloads.');
})().catch(async error=>{
  console.error(error.stack);if(runningBrowser)await runningBrowser.close().catch(()=>{});
  if(runningServer?.listening)await new Promise(resolve=>runningServer.close(resolve));process.exitCode=1;
});
