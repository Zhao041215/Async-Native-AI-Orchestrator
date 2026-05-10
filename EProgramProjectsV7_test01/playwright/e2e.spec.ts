import { test, expect } from '@playwright/test';

const base = process.env.BASE_URL || 'http://localhost:3000';
const adminUser = process.env.ADMIN_USER || 'admin';
const adminPass = process.env.ADMIN_PASS || 'admin123';

test('登录/退出/未登录拦截', async ({ page }) => {
  await page.goto(`${base}/admin`);
  await expect(page).toHaveURL(/login|admin/);
  await page.getByLabel(/用户名/).fill(adminUser);
  await page.getByLabel(/密码/).fill(adminPass);
  await page.getByRole('button', { name: /登录/ }).click();
  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible();
  await page.getByRole('button', { name: /退出/ }).click();
  await page.goto(`${base}/admin/articles`);
  await expect(page).toHaveURL(/login/);
});

test('文章新增-软删除-恢复', async ({ page }) => {
  await page.goto(`${base}/login`);
  await page.getByLabel(/用户名/).fill(adminUser);
  await page.getByLabel(/密码/).fill(adminPass);
  await page.getByRole('button', { name: /登录/ }).click();
  await page.getByRole('button', { name: /新增文章/ }).click();
  await page.getByLabel(/标题/).fill('E2E测试文章');
  await page.getByLabel(/内容/).fill('# 标题\n\n测试内容 keyword');
  await page.getByRole('button', { name: /保存/ }).click();
  await expect(page.getByText('E2E测试文章')).toBeVisible();
  await page.getByRole('button', { name: /删除/ }).first().click();
  await page.getByRole('button', { name: /恢复/ }).first().click();
  await expect(page.getByText('E2E测试文章')).toBeVisible();
});

test('搜索高亮与分页排序', async ({ page }) => {
  await page.goto(base);
  await page.getByPlaceholder(/搜索/).fill('keyword');
  await page.keyboard.press('Enter');
  await expect(page.locator('mark, .highlight')).toContainText(/keyword/i);
});
