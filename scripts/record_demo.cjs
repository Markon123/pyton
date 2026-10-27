/* Optional local recording: npm install --prefix .local/demo-tools playwright
   PLAYWRIGHT_MODULE may point to an existing installation for the test runner. */
const path = require('path');
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(__dirname, '..', '.local', 'demo-tools', 'node_modules', 'playwright'));
const base = process.env.DEMO_BASE_URL || 'http://127.0.0.1:8000';
const target = path.join(__dirname, '..', 'docs', 'demo.webm');
const temporary = path.join(__dirname, '..', '.local', 'demo-recording');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
  const context = await browser.newContext({viewport: {width: 1440, height: 960}, recordVideo: {dir: temporary, size: {width: 1280, height: 854}}});
  const page = await context.newPage();
  const video = page.video();
  const failures = [];
  page.on('pageerror', e => failures.push(e.message));
  async function show(route, caption, seconds=9) {
    const response = await page.goto(base + route);
    if (!response || response.status() !== 200) throw new Error(`Demo page failed: ${route}`);
    await page.evaluate(text => {
      const banner = document.createElement('div');
      banner.textContent = text;
      banner.setAttribute('role', 'status');
      Object.assign(banner.style, {position:'fixed', bottom:'20px', left:'290px', right:'25px', background:'#172726', color:'#fff', borderLeft:'5px solid #d6ff61', padding:'18px 22px', borderRadius:'12px', font:'18px/1.5 sans-serif', zIndex:'9999', boxShadow:'0 4px 25px #0003'});
      document.body.appendChild(banner);
    }, caption);
    await page.waitForTimeout(seconds * 1000);
    await page.mouse.wheel(0, 350);
    await page.waitForTimeout(1200);
    console.log('Recorded:', route);
  }
  try {
    await page.goto(base + '/login/');
    await page.locator('[name=username]').fill('federation');
    await page.locator('[name=password]').fill('FspDemo2026!');
    await Promise.all([page.waitForURL(base + '/'), page.locator('button[type=submit]').first().click()]);
    await show('/', 'ОренКод — единая платформа ФСП. Все данные демонстрационные.');
    await show('/competitions/', 'Календарь всех шести дисциплин: публикация, возрастной допуск, приём заявок.');
    await show('/competitions/1/', 'Одобренные участники → результаты → протокол XLSX, DOCX и PDF.');
    await show('/qualifications/', 'Разряды и категории: основание, рассмотрение, приказ и срок действия.');
    await show('/rating/', 'Прозрачный рейтинг за сезон и формирование сборной региона.');
    await show('/training/', 'Программы подготовки, группы, журнал занятий и нормативы.');
    await show('/reports/', 'Численность по годам с историей организаций, дисциплин и муниципалитетов.');
    await show('/governance/appeals/', 'Апелляции и нарушения: доказательства, решение и история исправления протокола.');
    await show('/governance/awards/', 'Автоматический расчёт кандидатов на премию и подготовка ходатайств.');
    await show('/development/certificates/', 'Справки с QR-кодом и публичной проверкой минимального набора данных.');
    await show('/integrations/', 'API, партнёрские полномочия, импорт результатов и подписанные вебхуки.');
    await show('/identity/demo/', 'ФСП ID: OpenID Connect с PKCE и отдельным тестовым клиентом.');
    await show('/development/', 'Показатели развития региона со ссылками на источники и подтверждающие документы.');
    await show('/guide/', 'Публичный навигатор: где заниматься, какие дисциплины выбрать и как начать.');
    if (failures.length) throw new Error(failures.join('\n'));
  } finally {
    await context.close();
    await video.saveAs(target);
    await browser.close();
  }
  console.log('Saved:', target, fs.statSync(target).size, 'bytes');
})().catch(error => { console.error(error.message); process.exitCode=1; });
