# 库存核心表说明

- `Admin`: 管理员账号，`username` 唯一。
- `Session`: 会话鉴权，`token` 唯一，`adminId+expiresAt` 索引用于查有效会话。
- `Product`: 产品主数据。
  - `code` 唯一，对应“产品编号”
  - `stock` 当前库存
  - `lowStock` 最低库存警戒
  - `stock <= lowStock` 即库存不足
- `Movement`: 出入库流水。
  - `type`: `IN | OUT`
  - `quantity`: 记录变动数量（正整数，业务层校验）
  - `occurredAt`: 出/入库日期

## 关键查询
- 低库存列表: `WHERE stock <= lowStock AND isActive = true`
- 历史筛选: 按 `occurredAt`、`productId`、`type` 过滤
- 最近记录: 按 `occurredAt DESC, createdAt DESC`

## 约束建议
- 出库前事务校验: `stock >= quantity`
- 删除产品前确保无业务限制；当前流水采用 `onDelete: Restrict`
- 修改密码仅更新 `Admin.passwordHash`