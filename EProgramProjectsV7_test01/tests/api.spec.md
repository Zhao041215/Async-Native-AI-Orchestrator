# API自动化建议

可用 Jest + Supertest / Vitest + axios 实现。

```js
// 示例结构（请按实际接口改造）
describe('Auth API', () => {
  it('login success', async () => {
    // POST /api/auth/login
  })
})

describe('Article API', () => {
  it('create/list/update/delete', async () => {
    // POST /api/articles
    // GET /api/articles?page=1&pageSize=10&keyword=test
    // PUT /api/articles/:id
    // DELETE /api/articles/:id
  })
})
```
