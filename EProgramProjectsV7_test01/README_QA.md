# QA / E2E 运行说明

## 目标
验证登录鉴权、文章/分类管理、搜索高亮、上传、中文文案一致性、软删除恢复、分页排序与安全边界。

## 手工验收
参考：`docs/QA_测试与验收说明.md` 与 `docs/UI中文验收清单.md`

## Playwright
```bash
npm i -D @playwright/test
npx playwright install
BASE_URL=http://localhost:3000 ADMIN_USER=admin ADMIN_PASS=admin123 npx playwright test
```

## 建议补充
- 若项目有开放 API，可将文档中的用例落地为接口自动化。
- 上传测试需准备 jpg/png 与非法文件样本。
- 第三方编辑器若存在英文 tooltip，应记录为已知限制或二次汉化。