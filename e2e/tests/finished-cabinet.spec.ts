import { test, expect } from '@playwright/test';

test('team_1 читает завершённый кабинет без игровых команд', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto(process.env.ELEMENT_APP_URL ?? 'https://app-312909758.1cmycloud.com/applications/game-dev');
  await expect(page.getByRole('link', { name: 'Кабинет игрока', exact: true }), 'Нужен вход team_1').toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('link', { name: 'Пульт', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Кабинет игрока', exact: true }).click();
  const team = page.getByRole('textbox', { name: 'Команда', exact: true });
  await expect(team).toHaveValue('OData · team_1 · закрытие до срока · Команда 1');
  await expect(page.getByText(/OData · team_1 · закрытие до срока · раунд 1 · Завершена/)).toBeVisible();
  await expect(page.getByText('Игра завершена. Итог последнего раунда зафиксирован.', { exact: true })).toBeVisible();
  await page.getByText('ЗАКАЗ №4', { exact: true }).click();
  await expect(page.getByTestId('ПолучитьКнопка')).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByTestId('ГотовоКнопка')).toHaveAttribute('aria-disabled', 'true');
  await page.reload();
  await expect(team).toHaveValue('OData · team_1 · закрытие до срока · Команда 1');
  await expect(page.getByText(/раунд 1 · Завершена/)).toBeVisible();
});
