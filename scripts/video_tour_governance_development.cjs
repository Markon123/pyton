'use strict';

// Recorded UI actions only. All mutations go through the site's real forms.
module.exports = async function governanceAndDevelopment(ctx) {
  const {page, ids, manifest, assert} = ctx;
  const year = Number(manifest.year || new Date().getFullYear());
  const resultId = String(ids.appealable_result || ids.result);
  const evidence = ctx.evidence || (ctx.evidence = {});

  await ctx.login('athlete');
  await ctx.chapter('D5', 'Апелляция: от обращения к рассмотрению',
    'Спортсмен подаёт обращение по своему опубликованному результату.');
  await ctx.goto('/governance/appeals/');
  await ctx.focus(page.getByRole('heading', {name: 'Подать апелляцию', exact: true}));
  assert(await page.locator('select[name="result"] option').evaluateAll(
    (options, value) => options.some(option => option.value === value), resultId),
  'The video athlete must own the appealable published result.');
  await page.locator('select[name="result"]').selectOption(resultId);
  await page.locator('textarea[name="reason"]').fill(
    'Прошу повторно проверить начисление баллов по задаче № 3 и сверить итог с журналом проверки. Обращение создаётся на вымышленных данных для записи пользовательского сценария.');
  await ctx.say('Выбран собственный результат; указано основание обращения.', 4);
  await ctx.click(page.getByRole('button', {name: 'Отправить апелляцию', exact: true}));
  await page.waitForURL(/\/governance\/appeals\/\d+\/$/);
  const appealRoute = new URL(page.url()).pathname;
  ids.video_appeal = Number(appealRoute.match(/(\d+)\/$/)[1]);
  assert.match(await page.locator('main').innerText(), /Подана/);
  await ctx.say('Апелляция сохранена. У спортсмена видны номер, основание и статус «Подана».', 4);

  await ctx.login('federation');
  await ctx.goto('/governance/appeals/');
  await ctx.click(page.locator(`a[href="${appealRoute}"]`).first());
  await ctx.focus(page.getByRole('heading', {name: 'Рассмотреть апелляцию', exact: true}));
  await page.locator('select[name="status"]').selectOption('reviewed');
  await page.locator('textarea[name="reason"]').fill(
    'Обращение принято к рассмотрению. Федерация проверяет журнал проверки и представленные основания; итоговые место и баллы пока не изменяются.');
  await ctx.click(page.getByRole('button', {name: 'Сохранить решение', exact: true}));
  await page.waitForLoadState('domcontentloaded');
  assert.match(await page.locator('main').innerText(), /На рассмотрении/);
  assert.match(await page.locator('main').innerText(), /Опубликованный протокол не изменялся/);
  await ctx.say('Федерация сохранила мотивировку и статус «На рассмотрении». Протокол остаётся без изменений.', 5);
  evidence.appeal = {id: ids.video_appeal, status: 'reviewed', result: Number(resultId), result_changed: false};

  await ctx.chapter('D6', 'Показатели развития региона',
    'Период, численность, организации подготовки и проведённые соревнования.');
  await ctx.goto(`/development/?year=${year}`);
  await ctx.focus(page.locator('.stats-grid'));
  const currentMetrics = await page.locator('.stats-grid .stat-card').allTextContents();
  await ctx.say(`Показатели за ${year} год строятся по записям платформы; источники названы на странице.`, 4);
  await page.locator('form.filter-bar input[name="year"]').fill(String(year - 1));
  await ctx.click(page.getByRole('button', {name: 'Показать', exact: true}));
  await page.waitForLoadState('domcontentloaded');
  const previousMetrics = await page.locator('.stats-grid .stat-card').allTextContents();
  assert.notDeepEqual(currentMetrics, previousMetrics, 'The reporting period should update the actual metric labels.');
  await ctx.focus(page.locator('.stats-grid'));
  await ctx.say(`После смены периода отображаются сведения за ${year - 1} год и предыдущий год.`, 4);
  evidence.regionalPeriods = {current: year, previous: year - 1, currentMetrics, previousMetrics};

  await ctx.login('athlete');
  await ctx.chapter('D7', 'Личный путь спортсмена',
    'Текущий разряд, условия следующего шага, подходящие старты и справочник.');
  await ctx.goto(`/development/pathway/?athlete=${ids.athlete}`);
  assert.match(await page.locator('main').innerText(), /Путь спортсмена/);
  await ctx.focus(page.getByRole('heading', {name: 'Что требуется для следующего шага', exact: true}));
  await ctx.say('Платформа показывает внесённые критерии следующей ступени. Решение о присвоении требует проверки федерацией.', 5);
  await ctx.focus(page.getByRole('heading', {name: 'Подходящие соревнования', exact: true}));
  await ctx.say('Будущие старты подбираются по возрасту и дисциплинам профиля.', 3);
  await ctx.focus(page.getByRole('heading', {name: 'Понятия простым языком', exact: true}));
  await ctx.say('Справочник объясняет разряд, уровень соревнования, роли и антидопинговые правила.', 4);
  evidence.pathway = {athlete: Number(ids.athlete), viewed: true};

  await ctx.login('federation');
  await ctx.chapter('D8', 'Премии Губернатора',
    'Основания кандидатов, настроенные правила и подготовка ходатайства.');
  await ctx.goto(`/governance/awards/?year=${year}`);
  await ctx.focus(page.getByRole('heading', {name: 'Автоматически найденные кандидаты', exact: true}));
  const prepare = page.getByRole('button', {name: 'Подготовить', exact: true}).first();
  assert(await prepare.count(), 'Prepared synthetic data must provide at least one award candidate.');
  await ctx.say('Кандидаты найдены по опубликованным результатам. В таблице показаны основание, правило и расчёт.', 5);
  await ctx.focus(page.getByRole('heading', {name: 'Настраиваемые правила', exact: true}));
  await ctx.say('На записи используются условные правила. Официальные суммы и источники проверяются перед подачей.', 5);
  await ctx.click(prepare);
  await page.waitForURL(/\/governance\/awards\/\d+\/$/);
  const petitionRoute = new URL(page.url()).pathname;
  ids.video_petition = Number(petitionRoute.match(/(\d+)\/$/)[1]);
  await ctx.focus(page.getByRole('heading', {name: 'Основание и документы', exact: true}));
  assert.match(await page.locator('main').innerText(), /Снимок расчёта на момент подготовки/);
  await ctx.say('Ходатайство сохраняет снимок расчёта и документы. Автоматической подачи в государственные системы нет.', 5);
  evidence.awardPetition = {id: ids.video_petition, viewed: true};

  await ctx.login('athlete');
  await ctx.chapter('D10', 'Справка об участии с QR-кодом',
    'Выдача настоящего PDF и публичная проверка номера без входа.');
  await ctx.goto('/development/certificates/');
  await ctx.focus(page.getByRole('heading', {name: 'Сформировать справку', exact: true}));
  const certificateResult = String(ids.result);
  assert(await page.locator('select[name="result"] option').evaluateAll(
    (options, value) => options.some(option => option.value === value), certificateResult),
  'The certificate result must be visible to the video athlete.');
  await page.locator('select[name="result"]').selectOption(certificateResult);
  await page.locator('input[name="public_consent"]').check();
  await ctx.say('Спортсмен выбирает свой опубликованный результат и соглашается на проверку минимальных сведений.', 4);
  // Issuing the form redirects directly to an attachment response, so the
  // submit itself is the actual download (there is no page navigation).
  const certificateFile = await ctx.download(page.getByRole('button', {name: 'Сформировать PDF', exact: true}), 'participation-with-qr.pdf');
  await ctx.goto('/development/certificates/');
  const certificateCard = page.locator('article.qualification-card').filter({has: page.getByRole('link', {name: 'Скачать PDF с QR', exact: true})}).first();
  await certificateCard.waitFor();
  assert.match(await certificateCard.innerText(), /Действительна/);
  const verifyHref = await certificateCard.getByRole('link', {name: 'Публичная проверка', exact: true}).getAttribute('href');
  const verifyURL = new URL(verifyHref, ctx.base);
  assert.equal(verifyURL.origin, new URL(ctx.base).origin);
  ids.video_certificate_uuid = verifyURL.pathname.match(/verify\/([^/]+)\//)[1];
  await ctx.viewFile(certificateFile, 'Скачанная справка: номер, результат и QR-код');
  await ctx.say('Открыт фактически скачанный PDF. QR ведёт к проверке уникального номера справки.', 5);
  await ctx.goto('/development/certificates/');
  await ctx.click(page.getByRole('button', {name: 'Выйти', exact: true}));
  await ctx.goto(verifyURL.pathname);
  assert.match(await page.locator('body').innerText(), /Действительна/);
  assert.match(await page.locator('body').innerText(), /Участник, инициалы/);
  await ctx.say('Проверка доступна без входа: статус, инициалы, соревнование и результат. Контакты и дата рождения не раскрываются.', 5);
  evidence.certificate = {number: ids.video_certificate_uuid, result: Number(certificateResult), publicVerification: true, pdf: certificateFile};

  await ctx.chapter('D11', 'Навигатор для новичка',
    'Публичная информация, дисциплины, организации и путь к регистрации.');
  await ctx.goto('/guide/');
  assert.match(await page.locator('main').innerText(), /Твой первый старт/);
  assert.equal(await page.getByRole('button', {name: 'Выйти', exact: true}).count(), 0, 'The navigator should be shown without a signed-in session.');
  await ctx.focus(page.getByRole('heading', {name: 'Шесть дисциплин — больше возможностей', exact: true}));
  await ctx.say('Навигатор знакомит с шестью дисциплинами спортивного программирования.', 4);
  await ctx.focus(page.getByRole('heading', {name: 'Четыре шага до первого результата', exact: true}));
  await ctx.say('Маршрут новичка: профиль, команда, подходящий старт и отслеживание результата.', 4);
  await ctx.focus(page.getByRole('heading', {name: 'Где заниматься', exact: true}));
  await ctx.say('Здесь видны организации региона и опубликованные ближайшие старты.', 4);
  const register = page.getByRole('link', {name: 'Зарегистрироваться для участия', exact: true}).first();
  if (await register.count()) await ctx.click(register);
  else await ctx.goto('/register/');
  await page.getByRole('heading', {name: 'Создать аккаунт', exact: true}).waitFor();
  await ctx.say('Самостоятельно регистрируются спортсмены, тренеры и судьи. Федерацию создаёт министерство.', 5);
  evidence.navigator = {anonymous: true, registrationFormViewed: true};
};
