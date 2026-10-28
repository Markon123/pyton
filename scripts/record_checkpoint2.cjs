/* Record the real checkpoint-2 workflow on an isolated, seeded local database.
   See docs/checkpoint-2.md. Captions describe actions; application data comes
   from the normal forms. No request interception or direct database writes are used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(__dirname, '..', '.local', 'checkpoint2-tools', 'node_modules', 'playwright'));

const root = path.resolve(__dirname, '..');
const base = process.env.CHECKPOINT_BASE_URL || 'http://127.0.0.1:8012';
const output = process.env.CHECKPOINT_OUTPUT_DIR || path.join(root, '.local', 'checkpoint2');
const holdScale = Number(process.env.CHECKPOINT_HOLD_SCALE || '1');
const title = process.env.CHECKPOINT_TITLE || 'Кубок ОренКод · чекпоинт 2';
const regulation = process.env.CHECKPOINT_REGULATION || path.join(output, 'regulation.docx');
const score = '142.50';
const password = 'FspDemo2026!';

if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) {
  throw new Error('Recording requires a local demonstration server with fictitious data.');
}
assert(Number.isFinite(holdScale) && holdScale >= 0, 'Invalid hold scale');
assert(fs.existsSync(regulation), 'Create the demonstration regulation first.');
fs.mkdirSync(output, {recursive: true});

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    slowMo: 90,
  });
  const context = await browser.newContext({
    viewport: {width: 1440, height: 960},
    recordVideo: {dir: path.join(output, 'raw'), size: {width: 1440, height: 960}},
    locale: 'ru-RU', timezoneId: 'Asia/Yekaterinburg', acceptDownloads: true,
  });
  await context.addInitScript(() => {
    window.checkpointCaption = function (value) {
      if (value) sessionStorage.setItem('checkpoint2-caption', JSON.stringify(value));
      const saved = sessionStorage.getItem('checkpoint2-caption');
      if (!saved || !document.body) return;
      let box = document.getElementById('checkpoint2-caption');
      if (!box) {
        box = document.createElement('aside');
        box.id = 'checkpoint2-caption';
        box.setAttribute('aria-label', 'Пояснение к демонстрации');
        Object.assign(box.style, {
          position: 'fixed', bottom: '16px', left: '280px', right: '24px',
          zIndex: '99999', padding: '16px 22px', borderRadius: '12px',
          background: '#172726f5', color: 'white', borderLeft: '5px solid #d6ff61',
          boxShadow: '0 4px 28px #0004', font: '19px/1.4 Arial, sans-serif',
          pointerEvents: 'none',
        });
        document.body.appendChild(box);
      }
      const {heading, text} = JSON.parse(saved);
      box.replaceChildren();
      const label = document.createElement('strong');
      label.textContent = heading;
      label.style.cssText = 'display:block;color:#d6ff61;font-size:17px;margin-bottom:5px';
      const paragraph = document.createElement('span');
      paragraph.textContent = text;
      box.append(label, paragraph);
    };
    document.addEventListener('DOMContentLoaded', () => window.checkpointCaption());
  });
  const page = await context.newPage();
  const video = page.video();
  const errors = [];
  const chapters = [];
  const started = Date.now();
  let successful = false;
  let competitionPath;
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.request().isNavigationRequest() && response.status() >= 400)
      errors.push(`Navigation HTTP ${response.status()}: ${response.url()}`);
    if (response.headers()['x-action-error']) errors.push(`Action failed: ${response.url()}`);
  });
  async function caption(heading, text) {
    chapters.push({seconds: Math.round((Date.now() - started) / 1000), heading, text});
    await page.evaluate(value => window.checkpointCaption(value), {heading, text});
    console.log(heading, '—', text);
  }
  async function pause(seconds) { await page.waitForTimeout(seconds * holdScale * 1000); }
  async function focus(locator) {
    await locator.evaluate(element => element.scrollIntoView({block: 'center', behavior: 'instant'}));
    await page.waitForTimeout(250);
  }
  async function navigate(route) {
    const response = await page.goto(base + route);
    assert.equal(response.status(), 200, `Cannot open ${route}`);
  }
  async function submit(button, route) {
    await Promise.all([
      page.waitForURL(base + route, {waitUntil: 'load'}),
      button.click(),
    ]);
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.message.error, .errorlist').count(), 0, 'Form or workflow error');
  }
  async function login(username) {
    if (await page.getByRole('button', {name: 'Выйти', exact: true}).count()) {
      await submit(page.getByRole('button', {name: 'Выйти', exact: true}), '/login/');
    } else {
      await navigate('/login/');
    }
    await page.locator('[name=username]').fill(username);
    await page.locator('[name=password]').fill(password);
    await pause(1);
    await submit(page.locator('button[type=submit]').first(), '/');
    assert.equal(await page.locator('.role-strip b').innerText(), username === 'athlete' ? 'Спортсмен' : 'Федерация');
  }
  try {
    await navigate('/login/');
    await caption('ЧЕКПОИНТ № 2 · СКВОЗНОЙ СЦЕНАРИЙ', 'Создание соревнования → заявка спортсмена → одобрение → результат → протокол → личная история. Все данные вымышлены.');
    await pause(6);
    await login('federation');
    await caption('1 / 6 · ФЕДЕРАЦИЯ СОЗДАЁТ СОРЕВНОВАНИЕ', 'Это отдельный кабинет организатора. Создаём новый старт, а не открываем готовую демонстрационную запись.');
    await page.locator('a.nav-link[href="/competitions/"]').click();
    await page.getByRole('link', {name: /Создать соревнование/}).click();
    await page.locator('[name=title]').fill(title);
    await page.locator('[name=description]').fill('Демонстрационный турнир для чекпоинта № 2. Все участники и результаты вымышлены.');
    const algorithm = page.locator('label').filter({hasText: 'Программирование алгоритмическое'}).locator('input[type=checkbox]');
    await algorithm.check();
    const disciplineId = await algorithm.inputValue();
    const today = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Yekaterinburg'}).format(new Date());
    const previous = new Date(today + 'T12:00:00Z');
    previous.setUTCDate(previous.getUTCDate() - 7);
    for (const name of ['starts_on', 'ends_on', 'registration_closes']) await page.locator(`[name=${name}]`).fill(today);
    await page.locator('[name=registration_opens]').fill(previous.toISOString().slice(0, 10));
    await pause(4);
    await page.locator('[name=location]').fill('Оренбург · вымышленная демонстрационная площадка');
    await page.locator('[name=format]').selectOption('online');
    await page.locator('[name=level]').selectOption('regional');
    if (await page.locator('select[name=kind]').count()) await page.locator('[name=kind]').selectOption({label: 'Кубок'});
    else await page.locator('[name=kind]').fill('Кубок');
    await page.locator('[name=age_min]').fill('12');
    await page.locator('[name=age_max]').fill('99');
    await page.locator('[name=calendar_plan]').selectOption('regional');
    await page.locator('[name=plan_number]').fill('ДЕМО-ЧП2-001');
    await page.locator('[name=regulation]').setInputFiles(regulation);
    await page.locator('[name=state]').selectOption('draft');
    await pause(4);
    await Promise.all([
      page.waitForURL(/\/competitions\/\d+\/$/),
      page.getByRole('button', {name: /^Сохранить/}).click(),
    ]);
    competitionPath = new URL(page.url()).pathname;
    assert.equal(await page.locator('h1').innerText(), title);
    await page.screenshot({path: path.join(output, '01-created.png')});
    await submit(page.locator('button[name=action][value=publish_competition]'), competitionPath);
    await submit(page.locator('button[name=action][value=open]'), competitionPath);
    await caption('1 / 6 · СОРЕВНОВАНИЕ СОЗДАНО', 'Положение загружено, соревнование опубликовано, приём заявок открыт.');
    await pause(7);

    await caption('2 / 6 · ВХОДИМ КАК СПОРТСМЕН', 'Выходим из кабинета федерации и входим под отдельной учётной записью athlete — Александр Тестов.');
    await login('athlete');
    await navigate(competitionPath);
    await focus(page.locator('#apply'));
    await caption('2 / 6 · СПОРТСМЕН ПОДАЁТ ЗАЯВКУ', 'Выбираем свою дисциплину и отправляем заявку. Решение за спортсмена не принимается автоматически.');
    await page.locator('#apply [name=discipline]').selectOption(disciplineId);
    await pause(6);
    await submit(page.getByRole('button', {name: /Отправить заявку/}), competitionPath);
    const applicationRow = page.locator('tr').filter({hasText: 'Александр Тестов'}).first();
    assert.match(await applicationRow.innerText(), /На рассмотрении/);
    await focus(applicationRow);
    await caption('2 / 6 · ЗАЯВКА НА РАССМОТРЕНИИ', 'Заявка сохранена и видна спортсмену. Следующий шаг выполняет федерация.');
    await page.screenshot({path: path.join(output, '02-pending.png')});
    await pause(7);

    await caption('3 / 6 · ФЕДЕРАЦИЯ РАССМАТРИВАЕТ ЗАЯВКУ', 'Возвращаемся в кабинет федерации: у организатора появляются действия одобрения и отклонения.');
    await login('federation');
    await navigate(competitionPath);
    await focus(page.locator('button[name=status][value=approved]'));
    await pause(5);
    await submit(page.locator('button[name=status][value=approved]'), competitionPath);
    const approvedRow = page.locator('tr').filter({hasText: 'Александр Тестов'}).first();
    assert.match(await approvedRow.innerText(), /Одобрена/);
    await focus(approvedRow);
    await caption('3 / 6 · ЗАЯВКА ОДОБРЕНА', 'Спортсмен допущен к соревнованию. Закрываем регистрацию и переходим к результатам.');
    await page.screenshot({path: path.join(output, '03-approved.png')});
    await pause(6);
    await submit(page.locator('button[name=action][value=close]'), competitionPath);

    await caption('4 / 6 · ФЕДЕРАЦИЯ ВНОСИТ РЕЗУЛЬТАТ', 'Результат можно внести только по одобренной заявке. Укажем первое место и 142,50 балла.');
    await page.getByText('Внести или обновить результат', {exact: true}).click();
    const resultForm = page.locator('form[action$="/results/"]');
    await resultForm.locator('[name=application]').selectOption({index: 1});
    await resultForm.locator('[name=place]').fill('1');
    await resultForm.locator('[name=score]').fill(score);
    await focus(resultForm);
    await pause(6);
    await submit(page.getByRole('button', {name: 'Сохранить результат', exact: true}), competitionPath);
    const resultRow = page.locator('#results tbody tr').filter({hasText: 'Александр Тестов'});
    assert.match(await resultRow.innerText(), /142,5/);
    await focus(resultRow);
    await pause(5);

    await caption('5 / 6 · ПУБЛИКУЕМ ПРОТОКОЛ', 'После сохранения результата публикуем полный протокол. Опубликованные результаты зафиксированы.');
    await submit(page.locator('button[name=action][value=publish]'), competitionPath);
    assert.match(await page.locator('#results').innerText(), /Протокол опубликован/);
    await focus(page.locator('#results'));
    await pause(7);
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#results .download-row').getByRole('link', {name: 'PDF', exact: true}).click();
    const download = await downloadPromise;
    const protocolPath = path.join(output, 'protocol.pdf');
    await download.saveAs(protocolPath);
    assert.equal(await download.failure(), null);
    const protocol = fs.readFileSync(protocolPath);
    assert.equal(protocol.subarray(0, 5).toString(), '%PDF-');
    const text = execFileSync('pdftotext', ['-layout', protocolPath, '-'], {encoding: 'utf8'});
    assert(text.includes(title), 'Protocol is missing the competition');
    assert(text.includes('Александр Тестов'), 'Protocol is missing the athlete');
    assert.match(text, /142[.,]50?/, 'Protocol is missing the actual score');
    await caption('5 / 6 · ПРОТОКОЛ СФОРМИРОВАН И СКАЧАН', 'Получен настоящий PDF: новый турнир, Александр Тестов, первое место, 142,50 балла. Доступны также XLSX и DOCX.');
    await page.screenshot({path: path.join(output, '04-protocol.png')});
    await pause(8);

    // Show the actual downloaded PDF, rendered without changing its contents.
    // This is a document viewer in the recording, not an application screen.
    const preview = path.join(output, 'protocol-preview');
    execFileSync('pdftoppm', ['-f', '1', '-singlefile', '-scale-to', '1400', '-png', protocolPath, preview]);
    const png = fs.readFileSync(preview + '.png').toString('base64');
    await page.evaluate(({png, title}) => {
      const viewer = document.createElement('main');
      viewer.style.cssText = 'position:fixed;inset:0;background:#edf0e7;padding:24px;text-align:center;overflow:hidden';
      const heading = document.createElement('h1');
      heading.textContent = 'Просмотр скачанного PDF · ' + title;
      heading.style.cssText = 'font:600 24px/1.4 Arial,sans-serif;margin:0 0 12px;color:#172726';
      const image = document.createElement('img');
      image.src = 'data:image/png;base64,' + png;
      image.alt = 'Первая страница PDF, скачанного из приложения';
      image.style.cssText = 'max-width:1300px;max-height:740px;object-fit:contain;box-shadow:0 4px 24px #0002';
      viewer.append(heading, image);
      document.body.replaceChildren(viewer);
    }, {png, title});
    await caption('5 / 6 · СОДЕРЖИМОЕ ПОЛУЧЕННОГО PDF', 'Это скачанный из приложения документ: название турнира, участник, место и баллы совпадают с внесённым результатом.');
    await pause(18);
    await navigate(competitionPath);

    await caption('6 / 6 · РЕЗУЛЬТАТ В ЛИЧНОЙ ИСТОРИИ', 'Снова входим отдельным спортсменом и открываем его спортивную карточку.');
    await login('athlete');
    await page.locator('a.user-summary').click();
    await page.getByRole('link', {name: /Спортивная карточка/}).click();
    await focus(page.getByRole('heading', {name: 'История выступлений', exact: true}));
    const historyRow = page.locator('tr').filter({has: page.getByRole('link', {name: title, exact: true})});
    assert.equal(await historyRow.count(), 1);
    assert.match(await historyRow.innerText(), /142,5/);
    assert.equal(await historyRow.locator('.place-badge').innerText(), '1');
    await focus(historyRow);
    await caption('6 / 6 · СЦЕНАРИЙ ЗАВЕРШЁН', 'В истории спортсмена появился созданный в этом видео турнир: первое место, 142,50 балла. Полная цепочка пройдена через интерфейс.');
    await page.screenshot({path: path.join(output, '05-history.png')});
    await pause(10);
    if (holdScale === 1) {
      const remaining = 125000 - (Date.now() - started);
      if (remaining > 0) await page.waitForTimeout(remaining);
    }
    assert.deepEqual(errors, [], 'Browser or server failures');
    fs.writeFileSync(path.join(output, 'evidence.json'), JSON.stringify({
      recorded_at: new Date().toISOString(), competition: title,
      competition_path: competitionPath, athlete: 'Александр Тестов',
      place: 1, score, verified_pdf: true, verified_history: true,
      browser_errors: errors, chapters,
    }, null, 2) + '\n');
    successful = true;
  } catch (error) {
    await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
    throw error;
  } finally {
    await context.close();
    if (successful) await video.saveAs(path.join(output, 'checkpoint-2.webm'));
    await browser.close();
  }
  console.log('Verified workflow video:', path.join(output, 'checkpoint-2.webm'));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
