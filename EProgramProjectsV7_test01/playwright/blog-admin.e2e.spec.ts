import { test, expect } from '@playwright/test';

const user = process.env.E2E_USER || 'admin';
const pass = process.env.E2E_PASS || '123456';

test('登录并进入文章管理', async ({ page }) => {
  await page.goto('/login');
  await page.getByPlaceholder(/用户名|账号/).fill(user);
  await page.getByPlaceholder(/密码/).fill(pass);
  await page.getByRole('button', { name: /登录/ }).click();
  await expect(page).toHaveURL(/dashboard|home|admin/);
  await page.getByRole('link', { name: /文章/ }).click();
  await expect(page.getByText(/文章管理|文章列表/)).toBeVisible();
});

test('文章新增-搜索-删除流程', async ({ page }) => {
  const title = `E2E测试文章-${Date.now()}`;
  await page.goto('/login');
  await page.getByPlaceholder(/用户名|账号/).fill(user);
  await page.getByPlaceholder(/密码/).fill(pass);
  await page.getByRole('button', { name: /登录/ }).click();
  await page.getByRole('link', { name: /文章/ }).click();
  await page.getByRole('button', { name: /新增|创建/ }).click();
  await page.getByPlaceholder(/标题/).fill(title);
  const editor = page.locator('textarea, [contenteditable="true"]').first();
  await editor.fill('自动化测试内容');
  await page.getByRole('button', { name: /发布|保存/ }).click();
  await page.getByPlaceholder(/搜索/).fill(title);
  await page.getByRole('button', { name: /搜索/ }).click();
  await expect(page.getByText(title)).toBeVisible();
  await page.getByRole('button', { name: /删除/ }).first().click();
  await page.getByRole('button', { name: /确定|确认/ }).click();
});

test('分页与上传入口可用', async ({ page }) => {
  await page.goto('/login');
  await page.getByPlaceholder(/用户名|账号/).fill(user);
  await page.getByPlaceholder(/密码/).fill(pass);
  await page.getByRole('button', { name: /登录/ }).click();
  await page.getByRole('link', { name: /文章/ }).click();
  const next = page.getByRole('button', { name: /下一页/ });
  if (await next.isVisible()) await next.click();
  await page.getByRole('button', { name: /新增|创建/ }).click();
  await expect(page.getByText(/上传|封面|图片/)).toBeVisible();
});
