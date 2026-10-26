import { test, expect } from '@playwright/test';

const appURL = process.env.ELEMENT_APP_URL ?? 'https://app-312909758.1cmycloud.com/applications/game-dev';
const cases = [
  { name: 'закрытие до срока', game: 'OData · team_1 · закрытие до срока', values: ['1', '75000', '1', '75000', '0', '0'] },
  { name: 'позднее закрытие', game: 'OData · две команды · 25.10.2026', values: ['2', '335000', '0', '0', '2', '335000'] },
];

for (const fixture of cases) {
  test(`сохранённый раунд 0: ${fixture.name}`, async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto(appURL);
    await expect(page.getByRole('link', { name: 'Результаты', exact: true }), 'Нужен вход ведущего').toBeVisible({ timeout: 20_000 });
    await page.getByRole('link', { name: 'Результаты', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Результаты игры', exact: true })).toBeVisible();
    const game = page.getByRole('textbox', { name: 'Игра', exact: true });
    await game.fill(fixture.game);
    await game.press('ArrowDown');
    await page.getByText(fixture.game, { exact: true }).click();
    await game.press('Tab');
    const round = page.getByRole('textbox', { name: 'Раунд', exact: true });
    await round.press('ArrowDown');
    await page.getByText('Раунд 0', { exact: true }).click();
    await round.press('Tab');
    // Element exposes custom buttons without role=button; their observed root is focusable.
    await page.getByText('Показать результаты', { exact: true }).locator('../..').press('Enter');
    const row = page.locator('[data-component="table-row"]').filter({ has: page.getByText('Команда 1', { exact: true }) });
    await expect(row).toBeVisible();
    await expect.poll(async () => (await row.locator('[data-component="table-cell"]').allTextContents())
      .slice(1, 7).map(value => value.replace(/\s/g, ''))).toEqual(fixture.values);
  });
}
