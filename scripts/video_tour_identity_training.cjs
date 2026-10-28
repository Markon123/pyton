// Real browser chapters D1–D4. All writes use the website's ordinary forms.
const fs = require('node:fs');
const path = require('node:path');

module.exports = async function identityAndTraining(ctx) {
  const {page, manifest, ids, output, assert} = ctx;
  const goto = route => ctx.goto(route);
  const click = locator => ctx.click(locator);
  const say = (text, seconds = 3) => ctx.say(text, seconds);
  const focus = locator => ctx.focus(locator);
  const h1 = () => page.locator('h1');
  const contains = async text => (await page.locator('body').innerText()).includes(text);
  const primaryAthlete = String(ids.athlete);

  await ctx.login('federation');
  await ctx.chapter('D1', 'Региональный ФСП ID',
    'Вход приложения по OpenID Connect: согласие пользователя, проверенный ответ и отзыв доступа.');
  await goto('/identity/');
  assert.match(await h1().innerText(), /ФСП ID/);
  await focus(page.locator('section.panel').first());
  await say('Федерация управляет подключениями приложений. Для записи используется локальный тестовый клиент.', 4);

  await ctx.login('athlete');
  await goto('/identity/demo/');
  await click(page.getByRole('button', {name: 'Войти через ФСП ID', exact: true}));
  await page.waitForURL(url => url.pathname === '/o/authorize/', {timeout: 45000});
  await focus(page.locator('section.panel').first());
  await say('Спортсмен видит запрашиваемые сведения и сам разрешает их передачу. Пароль клиенту не передаётся.', 4);
  await click(page.getByRole('button', {name: 'Разрешить передачу данных', exact: true}));
  await page.waitForURL(url => url.pathname === '/identity/demo/', {timeout: 45000});
  await page.getByRole('heading', {name: 'Проверенный ID token', exact: true}).waitFor();
  const verified = page.locator('section.panel').filter({
    has: page.getByRole('heading', {name: 'Проверенный ID token', exact: true}),
  });
  await focus(verified);
  assert.match(await verified.innerText(), /sub/);
  await say('Ответ проверен: подпись, получатель, срок и пользователь. Поле sub — идентификатор спортсмена для этого входа.', 5);
  await focus(page.locator('section.panel').filter({
    has: page.getByRole('heading', {name: 'Ответ userinfo', exact: true}),
  }));
  await ctx.hold(3);
  await click(page.getByRole('link', {name: 'Мои разрешения и отзыв доступа', exact: true}));
  await page.getByRole('heading', {name: 'Мои разрешения ФСП ID', exact: true}).waitFor();
  await say('Доступ приложения можно отозвать вместе с его токенами.', 3);
  const revokeGrant = page.getByRole('button', {name: 'Отозвать доступ', exact: true});
  if (await revokeGrant.count()) {
    await click(revokeGrant.first());
    assert.equal(await revokeGrant.count(), 0);
    await say('Разрешение отозвано. Личный кабинет спортсмена продолжает работать.', 3);
  }

  await ctx.login('federation');
  await ctx.chapter('D2', 'API и интеграции',
    'Ограничения доступа, импорт результатов и настройки обмена без раскрытия секретов.');
  await goto('/integrations/');
  assert.match(await h1().innerText(), /API и интеграции/);
  await focus(page.locator('section.panel').filter({has: page.getByRole('heading', {name: 'Импорт результатов', exact: true})}));
  await say('Подключение реального внешнего партнёра не настроено. Показываем работающий импорт и доступные настройки API.', 5);

  let resultsCsv = manifest.files && (manifest.files.results_csv || manifest.files.resultsCsv);
  if (resultsCsv && !path.isAbsolute(resultsCsv)) resultsCsv = path.resolve(resultsCsv);
  if (!resultsCsv || !fs.existsSync(resultsCsv)) {
    let applicationId = ids.import_application;
    if (!applicationId) {
      await goto(`/competitions/${ids.import_competition}/`);
      const options = await page.locator('#id_application option').evaluateAll(nodes =>
        nodes.map(node => ({value: node.value, label: node.textContent})).filter(node => node.value));
      assert.ok(options.length, 'Import competition must have an approved application');
      applicationId = options[0].value;
      await goto('/integrations/');
    }
    fs.mkdirSync(output, {recursive: true});
    resultsCsv = path.join(output, 'results-import.csv');
    fs.writeFileSync(resultsCsv, `application,place,score\n${applicationId},1,188.75\n`, 'utf8');
  }
  const importForm = page.locator('form').filter({has: page.locator('input[name="action"][value="import"]')});
  await importForm.locator('select[name="competition"]').selectOption(String(ids.import_competition));
  await importForm.locator('input[name="file"]').setInputFiles(resultsCsv);
  await focus(importForm);
  await say('Выбираем закрытое соревнование и файл CSV. В нём указаны одобренные заявки, места и баллы.', 4);
  await click(importForm.getByRole('button', {name: 'Проверить и импортировать', exact: true}));
  assert.ok(await contains('Импортировано результатов:'), 'Import must report actual saved results');
  await say('Результаты импортированы через форму. Система отдельно сообщает, что протокол ещё не опубликован.', 4);
  await goto(`/competitions/${ids.import_competition}/`);
  await focus(page.locator('#results'));
  assert.ok((await page.locator('#results tbody tr').count()) > 0);
  assert.equal(await page.locator('#results .table-empty').count(), 0);
  await say('В карточке соревнования появились импортированные места и баллы. Это сохранённые данные приложения.', 4);
  await goto('/integrations/');
  await focus(page.locator('section.panel').filter({has: page.getByRole('heading', {name: 'Подписаться на webhook', exact: true})}));
  await say('Для вебхуков требуются разрешённый HTTPS-адрес и получатель. Фактическая внешняя доставка здесь не заявляется.', 4);
  await click(page.getByRole('link', {name: 'OpenAPI JSON', exact: true}));
  await page.waitForURL(url => url.pathname === '/api/v1/schema/');
  assert.match(await page.locator('body').innerText(), /openapi|swagger|paths/);
  await say('Схема API доступна для разработчиков интеграций.', 3);

  await ctx.login('coach');
  await ctx.chapter('D3', 'Организация, группа и журнал занятий',
    'Тренер открывает свою группу, создаёт занятие и сохраняет посещаемость.');
  await goto('/training/');
  const groupLink = page.locator(`a[href="/training/groups/${ids.group}/"]`).first();
  await groupLink.waitFor();
  const groupRow = page.locator('tr').filter({has: groupLink});
  const organizationName = (await groupRow.locator('td').nth(2).innerText()).trim();
  await focus(groupRow);
  await say('В группе связаны организация, дисциплина, этап подготовки и назначенный тренер.', 3);

  await goto('/registry/?kind=organization');
  const organizationLink = ids.organization
    ? page.locator(`a[href="/organizations/${ids.organization}/"]`).first()
    : page.getByRole('link', {name: organizationName, exact: true}).first();
  await click(organizationLink);
  assert.match(await page.url(), /\/organizations\/\d+\/$/);
  await say('Карточка организации объединяет её участников и группы подготовки.', 4);
  const directGroupLink = page.locator(`a[href="/training/groups/${ids.group}/"]`).first();
  if (await directGroupLink.count()) await click(directGroupLink);
  else await goto(`/training/groups/${ids.group}/`);
  await focus(page.locator('section.panel').filter({has: page.getByRole('heading', {name: 'Спортсмены и готовность к переводу', exact: true})}));
  assert.ok(await contains('Спортсмены и готовность к переводу'));
  await ctx.hold(3);

  const topic = 'Практика: алгоритмы и командная работа';
  const existingLesson = page.getByRole('link', {name: topic, exact: true});
  if (await existingLesson.count()) {
    await click(existingLesson.first());
    await say('Открываем уже сохранённую запись журнала и актуализируем посещаемость.', 3);
  } else {
    await click(page.getByRole('link', {name: 'Добавить занятие', exact: true}));
    const day = /^\d{4}-\d{2}-\d{2}$/.test(String(manifest.today || ''))
      ? manifest.today : new Date().toISOString().slice(0, 10);
    await page.locator('#id_date').fill(day);
    await page.locator('#id_topic').fill(topic);
    await page.locator('#id_format').selectOption('offline');
    await focus(page.locator('form.form-stack'));
    await say('Записываем тему, дату и очный формат занятия в настоящую форму журнала.', 4);
    await click(page.locator('form.form-stack').getByRole('button', {name: /^Сохранить/}));
    await page.waitForURL(url => /^\/training\/sessions\/\d+\/$/.test(url.pathname));
  }
  const sessionMatch = new URL(page.url()).pathname.match(/\/sessions\/(\d+)\//);
  if (sessionMatch) ids.recorded_session = Number(sessionMatch[1]);
  assert.equal((await h1().innerText()).trim(), topic);
  const athleteCheckbox = page.locator(`input[name="present"][value="${primaryAthlete}"]`);
  assert.equal(await athleteCheckbox.count(), 1, 'Primary athlete must be enrolled on the lesson date');
  await athleteCheckbox.check();
  await focus(page.locator('form.form-stack'));
  await click(page.getByRole('button', {name: 'Сохранить посещаемость', exact: true}));
  assert.ok(await contains('Посещаемость сохранена.'));
  const attendanceSection = page.locator('section.panel').filter({has: page.getByRole('heading', {name: 'Зафиксированные отметки', exact: true})});
  await focus(attendanceSection);
  assert.match(await attendanceSection.innerText(), /Присутствовал/);
  await say('Посещаемость сохранена. В журнале виден участник и подтверждённая отметка о присутствии.', 4);

  await ctx.chapter('D4', 'Нормативы и аттестация',
    'Фиксация результата, сравнение с порогом и проверка готовности к следующему этапу.');
  await goto('/training/norms/');
  assert.match(await h1().innerText(), /Нормативы и аттестация/);
  await focus(page.locator('section.panel').first());
  await say('Справочник содержит пороги, единицы измерения и источник. Тестовые значения отмечены отдельно.', 4);
  await goto(`/training/groups/${ids.group}/`);
  await click(page.getByRole('link', {name: 'Внести норматив', exact: true}));
  await page.locator('#id_athlete').selectOption(primaryAthlete);
  let normId = ids.norm;
  if (!normId) {
    const norms = await page.locator('#id_norm option').evaluateAll(nodes =>
      nodes.map(node => ({value: node.value, label: node.textContent})).filter(node => node.value));
    const speed = norms.find(node => /скорость.*печат|печат.*скорость/i.test(node.label));
    assert.ok(speed, 'Prepared speed assessment norm is required for a meaningful recording');
    normId = speed.value;
  }
  await page.locator('#id_norm').selectOption(String(normId));
  await page.locator('#id_value').fill('250');
  const testedOn = /^\d{4}-\d{2}-\d{2}$/.test(String(manifest.today || ''))
    ? manifest.today : new Date().toISOString().slice(0, 10);
  await page.locator('#id_tested_on').fill(testedOn);
  await focus(page.locator('form.form-stack'));
  await say('Тренер вносит фактический результат: 250 знаков в минуту. Сравнение с порогом выполняет приложение.', 4);
  await click(page.locator('form.form-stack').getByRole('button', {name: /^Сохранить/}));
  await page.waitForURL(url => url.pathname === `/training/groups/${ids.group}/`);
  const resultsSection = page.locator('section.panel').filter({has: page.getByRole('heading', {name: 'Сдача нормативов', exact: true})});
  await focus(resultsSection);
  assert.match(await resultsSection.innerText(), /250/);
  await say('В истории сохранены результат, порог на дату записи и итог сравнения.', 4);
  await goto(`/training/enrollments/${ids.enrollment}/transfer/`);
  await focus(page.locator('section.panel').first());
  assert.ok(await contains('Проверка обязательных нормативов'));
  if (await contains('Готов к переводу')) {
    await say('Все обязательные нормативы выполнены. Система разрешает переход к группе следующего этапа.', 5);
  } else {
    await say('Здесь проверяются все обязательные нормативы. Если требования не выполнены, перевод не разрешается.', 5);
  }
  await goto(`/training/groups/${ids.group}/`);
};
