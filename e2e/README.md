# Повторное чтение результатов OData

Тесты читают оставленные контрольные игры; новые игры и ERP-документы не создают.
Фактический прогон 25–26.10.2026 выполнен через управляемый браузер Codex и
проверен независимым OData-чтением. Эти specs сохранены для последующих запусков
Playwright; их автономный запуск требует отдельной локальной сессии входа.

Из этого каталога:

```sh
npm ci
npm exec -- playwright install chromium
ELEMENT_APP_URL=https://app-312909758.1cmycloud.com/applications/game-dev npm run auth
npm test -- tests/odata-results.spec.ts
```

В окне `auth` войдите ведущим. Для проверки игрока сохраните другую сессию:

```sh
ELEMENT_APP_URL=https://app-312909758.1cmycloud.com/applications/game-dev ELEMENT_AUTH_STATE=.auth/team1.json npm run auth
ELEMENT_AUTH_STATE=.auth/team1.json npm test -- tests/finished-cabinet.spec.ts
```

Войдите как `team_1`, дождитесь рабочей страницы и подтвердите вход в терминале.
Пароли в тестах отсутствуют. `.auth/` не читается тестовым кодом и исключён из Git.
Отчёты находятся в `playwright-report/<runID>/`, снимки ошибок — в
`test-results/<runID>/`. Проверка discovery: `npm test -- --list`.

Ожидаемые значения и ссылки: [протокол прогона](../docs/odata-cycle-2026-10-26.md).
