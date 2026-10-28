/* Real UI walkthrough on an isolated fictitious dataset.
   Preparation and reproduction: docs/video-tour.md.
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
const fixture = path.join(root, '.local', 'video-tour');
const output = process.env.VIDEO_TOUR_OUTPUT || path.join(fixture, 'recording');
const base = (process.env.VIDEO_BASE_URL || 'http://127.0.0.1:8018').replace(/\/$/, '');
const scale = Number(process.env.VIDEO_TOUR_HOLD_SCALE || '1');
const recording = process.env.VIDEO_TOUR_RECORD !== '0';
const only = process.env.VIDEO_TOUR_ONLY || '';
assert(!only || (!recording && only === 'roles'), 'A published recording must include the complete walkthrough.');
const python = process.env.VIDEO_TOUR_PYTHON || (process.platform==='win32'?'python':'python3');
const manifest = JSON.parse(fs.readFileSync(path.join(fixture, 'manifest.json'), 'utf8'));
const credentials = JSON.parse(fs.readFileSync(path.join(fixture, 'credentials.json'), 'utf8'));
const password = process.env.VIDEO_TOUR_PASSWORD || credentials.password;
const ids = manifest.ids;
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

  async function ratingAndRegistry(){
    await login('federation');
    const season=manifest.year||new Date().getFullYear();
    const rating=`/rating/?season=${season}&discipline=${ids.discipline}`;
    await goto(rating);
    await chapter('14','Рейтинг → карточка → сборная','Расчёт рейтинга основан на опубликованных результатах. Откроем карточку спортсмена и сформируем состав.');
    const athleteLink=page.locator(`a.table-name[href="/registry/${ids.athlete}/"]`);
    await click(athleteLink);
    assert((await page.locator('h1').innerText()).includes(manifest.users.athlete.display_name||manifest.users.athlete.name),'Unexpected athlete card.');
    await say('Карточка связывает спортсмена с дисциплиной, организацией, результатами и квалификацией.',4);
    await goto(rating);
    const members=page.locator('input[name=members]');
    assert((await members.count())>=2,'At least two eligible rating entries are required.');
    await members.nth(0).check();await members.nth(1).check();
    await focus(members.first());await hold(3);
    await click(page.getByRole('button',{name:/Сформировать сборную/}));
    const team=page.locator('section.team-panel');
    await focus(team);
    assert.match(await team.innerText(),/Сборная/);
    assert.equal(await team.locator('.team-members .person-line').count(),2,'Saved team is not the selected pair.');
    await say('Состав сохранён. В итоговом списке — два выбранных спортсмена; документы формируются из этого состава.',5);
    const teamPdf=await download(team.locator('a[href$="/pdf/"]').filter({hasText:'PDF'}),'team.pdf');
    await viewFile(teamPdf,'Состав сборной региона · скачанный PDF');

    await goto('/registry/?kind=athlete');
    await chapter('15','Реестр спортсменов и отчёты','Покажем поиск, личную карточку, динамику численности и реальные выгрузки.');
    const search=page.getByRole('textbox',{name:'Поиск по реестру'});
    const athleteName=manifest.users.athlete.display_name||manifest.users.athlete.name||'Тестов';
    await search.fill(athleteName);
    await hold(2);
    await click(page.getByRole('button',{name:'Применить',exact:true}));
    const result=page.locator(`a.table-name[href="/registry/${ids.athlete}/"]`);
    assert.equal(await result.count(),1,'Athlete search did not find the expected record.');
    await focus(result);await hold(3);await click(result);
    await say('Найденный участник: профиль, дисциплины, тренерская связь и история выступлений.',4);
    await goto(`/reports/?year_from=${season-1}&year_to=${season}`);
    await focus(page.getByRole('heading',{name:/Численность занимающихся/}));
    await say('Динамика численности за два года. Источники и выбранные разрезы показаны в отчёте.',5);
    const census=await download(page.locator('a[href*="/documents/census/0/xlsx/"]'),'athlete-count.xlsx');
    await viewFile(census,'Динамика численности спортсменов · XLSX');
    await goto(`/competitions/${ids.published_competition}/`);
    await focus(page.locator('#results'));
    await say('Возвращаемся на сайт. В соревновании опубликован протокол и доступны его выгрузки.',4);
    const protocol=await download(page.locator(`#results a[href="/documents/protocol/${ids.published_competition}/pdf/"]`),'competition-protocol.pdf');
    await viewFile(protocol,'Протокол соревнования · скачанный PDF');
    await goto(`/registry/${ids.athlete}/`);
    const athletePdf=await download(page.locator(`a[href="/documents/athlete/${ids.athlete}/pdf/"]`),'athlete-history.pdf');
    await viewFile(athletePdf,'Персональный отчёт спортсмена · скачанный PDF');
  }

  async function ministryChapter(){
    await goto('/');
    await chapter('16 / D9','Кабинет Министерства спорта','Выходим из федерации. Министерство просматривает сводные спортивные сведения и управляет аккаунтами федераций.');
    await login('ministry');await hold(3);
    await goto(`/governance/ministry/?year=${manifest.year}`);
    await say('Численность, опубликованный календарь, организации и составы сборной доступны в сводном кабинете.',5);
    await focus(page.getByRole('heading',{name:'Календарь соревнований',exact:true}));await hold(3);
    await focus(page.getByRole('heading',{name:'Составы сборной',exact:true}));await hold(3);
    await click(page.getByRole('link',{name:'Подробный отчёт',exact:true}));
    await say('Министерство может выбирать периоды и читать отчёт о численности. Форм редактирования спортсменов и результатов здесь нет.',5);
    assert.equal(await page.locator('form[method=post] input[name=score],form[method=post] input[name=place]').count(),0);
    await goto(`/competitions/${ids.published_competition}/`);
    assert.equal(await page.locator('form[action$="/results/"],a[href$="/edit/"],button[name=action]').count(),0,'Ministry was offered sports editing.');
    await focus(page.locator('#results'));
    await say('В карточке соревнования доступны опубликованные результаты и протокол. Управление заявками и изменение результатов отсутствуют.',4);
    await goto('/administration/federations/');
    await say('Отдельное полномочие главного аккаунта — создание и обслуживание федераций. Оно не даёт права изменять спортивные результаты.',4);
  }

  async function rolesAndJudging(){
    await login('federation');
    await goto('/registry/?kind=athlete');
    await chapter('18.1','Проверка ролей · федерация','Федерация работает с реестром и обычными ролями спортсмена, тренера и судьи. Служебные роли здесь не назначаются.');
    for(const role of ['athlete','coach','judge']){
      await goto(`/registry/?kind=${role}`);
      await say(`Реестр: ${role==='athlete'?'спортсмены':role==='coach'?'тренеры':'судьи'}. Найдём соответствующего участника.`,2);
      const link=page.locator(`a.table-name[href="/registry/${ids[role]}/"]`);
      await focus(link);await hold(2);
    }
    await goto(`/registry/${ids.athlete2}/edit/`);
    const ordinary=page.locator('input[name=roles]');
    assert((await ordinary.count())===3,'Unexpected role editor.');
    assert.deepEqual((await ordinary.evaluateAll(nodes=>nodes.map(n=>n.value))).sort(),['athlete','coach','judge']);
    await focus(ordinary.first());
    const judgeRole=page.locator('input[name=roles][value=judge]');
    await judgeRole.check();
    await say('У тестового участника добавим роль судьи. Роли федерации и министерства отсутствуют среди вариантов.',3);
    await click(page.getByRole('button',{name:/^Сохранить/}));
    await say('Изменение сохранено в карточке участника. Один аккаунт может совмещать обычные роли.',3);

    await chapter('18.2','Проверка ролей · спортсмен','Личный кабинет показывает собственные заявки, результаты, квалификации и документы.');
    await login('athlete');await goto('/profile/');
    await say('Спортсмен управляет своим профилем и видит собственную спортивную историю.',4);
    await goto(`/competitions/${ids.open_competition}/`);
    await focus(page.locator('#apply'));
    await say('В подходящем открытом соревновании спортсмену доступна подача своей заявки.',3);

    await chapter('18.3','Проверка ролей · тренер','Тренер работает с подтверждёнными спортсменами и собственными учебными группами. Региональную сборную формирует федерация.');
    await login('coach');await goto('/coaching/');
    await say('Подтверждённая связь по дисциплине определяет, за каких спортсменов может действовать тренер.',4);
    await goto(`/training/groups/${ids.group}/`);
    await say('Группа тренера: зачисленные участники, занятия, посещаемость и результаты нормативов.',4);

    await chapter('18.4 / D12','Судья · категория и документы','Судья видит собственные назначения, подаёт заявку на категорию и получает документы о судействе.');
    await login('judge');await goto('/qualifications/');
    const qualificationForm=page.locator('form').filter({has:page.getByRole('button',{name:'Отправить на рассмотрение',exact:true})});
    await qualificationForm.locator('[name=kind]').selectOption('category');
    await qualificationForm.locator('[name=athlete]').selectOption(String(ids.judge));
    const pendingTitles=await page.locator('.qualification-card').filter({has:page.locator('.badge.pending')}).locator('h3').allTextContents();
    const titles=await qualificationForm.locator('[name=requested_title] optgroup[label="Судейские категории"] option').evaluateAll(nodes=>nodes.filter(n=>n.value).map(n=>({value:n.value,text:n.textContent})));
    assert(titles.length,'No judge categories available.');
    const selectedTitle=titles.find(item=>!pendingTitles.map(value=>value.trim()).includes(item.value));
    assert(selectedTitle,'All judge categories already have a pending application.');
    await qualificationForm.locator('[name=requested_title]').selectOption(selectedTitle.value);
    await focus(qualificationForm);
    await say('Это заявка на судейскую категорию. Отправим её на рассмотрение федерации.',3);
    await click(qualificationForm.getByRole('button',{name:'Отправить на рассмотрение',exact:true}));
    await say('Заявка сохранена со статусом «На рассмотрении». Действующая категория и срок показаны отдельно.',4);
    await goto('/development/judging/');
    await say('Назначения судьи связаны с конкретными соревнованиями. Доступны состав коллегии и личная история завершённых стартов.',4);
    const panel=await download(page.locator(`a[href="/development/judging/panel/${ids.published_competition}/pdf/"]`),'judging-panel.pdf');
    await viewFile(panel,'Состав судейской коллегии · PDF');
    await goto('/development/judging/');
    const history=await download(page.locator(`a[href="/development/judging/history/${ids.judge}/pdf/"]`),'judging-history.pdf');
    await viewFile(history,'Личная история судейства · PDF');

    await goto('/');await chapter('18.5','Проверка ролей · министерство','Завершаем проверку сводным кабинетом министерства и возвращаемся в федерацию.');
    await login('ministry');await goto(`/governance/ministry/?year=${manifest.year}`);
    await say('Спортивные отчёты доступны для просмотра. Управление служебными аккаунтами выделено отдельно.',4);
    await login('federation');await goto('/');
    await chapter('ФИНИШ','ОренКод · запись завершена','Сборная, реестр, отчёты, дополнительные модули и права пользователей показаны на работающем сайте. Все данные в записи тестовые.');
    await hold(6);
  }

  try{
    await goto('/login/');
    await chapter('СТАРТ','ОренКод · подробная демонстрация','Рейтинг и сборная → реестр и отчёты → министерство → дополнительные модули → проверка ролей. Тестовые данные, настоящий интерфейс.');
    await hold(4);
    if(!only){
      await ratingAndRegistry();
      await ministryChapter();
      await require('./video_tour_identity_training.cjs')(ctx);
      await require('./video_tour_governance_development.cjs')(ctx);
    }
    await rolesAndJudging();
    assert.deepEqual(pageErrors,[],'Browser JavaScript errors.');
    assert.deepEqual(navigationErrors,[],'Server errors.');
    successful=true;
  }catch(error){
    await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});
    fs.writeFileSync(path.join(output,'failure.txt'),`${error.stack}\nURL: ${page.url()}\n`);
    throw error;
  }finally{
    fs.writeFileSync(path.join(output,'evidence.json'),JSON.stringify({successful,recording,partial:!!only,scale,viewport:{width:1440,height:960},durationSeconds:Number(((Date.now()-started)/1000).toFixed(2)),chapters,narrationEvents,downloads,pageErrors,navigationErrors,details:ctx.evidence||{}},null,2));
    await context.close();
    if(video)await video.saveAs(path.join(output,successful?'video-tour.webm':'incomplete.webm'));
    await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
  console.log('Completed:',chapters.length,'chapters;',downloads.length,'real documents.');
})().catch(async error=>{
  console.error(error.stack);
  if(runningBrowser)await runningBrowser.close().catch(()=>{});
  if(runningServer?.listening)await new Promise(resolve=>runningServer.close(resolve));
  process.exitCode=1;
});
