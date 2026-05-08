<?php
declare(strict_types=1);

final class EmployeeRepository
{
    public function __construct(private ?PDO $pdo = null)
    {
        $this->pdo = $pdo ?: Database::pdo();
    }

    public function paginate(string $keyword = '', int $page = 1, int $perPage = 10): array
    {
        $offset = max(0, ($page - 1) * $perPage);
        $where = '';
        $params = [];
        if ($keyword !== '') {
            $where = 'WHERE name LIKE ? OR employee_no LIKE ? OR department LIKE ?';
            $like = '%' . $keyword . '%';
            $params = [$like, $like, $like];
        }
        $count = $this->pdo->prepare("SELECT COUNT(*) AS total FROM employees {$where}");
        $count->execute($params);
        $total = (int) ($count->fetch()['total'] ?? 0);
        $statement = $this->pdo->prepare("SELECT * FROM employees {$where} ORDER BY id DESC LIMIT {$perPage} OFFSET {$offset}");
        $statement->execute($params);
        return ['items' => $statement->fetchAll(), 'total' => $total, 'page' => $page, 'pages' => max(1, (int) ceil($total / $perPage))];
    }

    public function find(int $id): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM employees WHERE id = ? LIMIT 1');
        $statement->execute([$id]);
        $row = $statement->fetch();
        return $row ?: null;
    }

    public function create(array $data): int
    {
        $statement = $this->pdo->prepare('INSERT INTO employees (name, employee_no, gender, department, position, phone, email, hire_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)');
        $statement->execute([$data['name'], $data['employee_no'], $data['gender'], $data['department'], $data['position'], $data['phone'], $data['email'], $data['hire_date'], $data['status']]);
        return (int) $this->pdo->lastInsertId();
    }

    public function update(int $id, array $data): void
    {
        $statement = $this->pdo->prepare('UPDATE employees SET name = ?, employee_no = ?, gender = ?, department = ?, position = ?, phone = ?, email = ?, hire_date = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?');
        $statement->execute([$data['name'], $data['employee_no'], $data['gender'], $data['department'], $data['position'], $data['phone'], $data['email'], $data['hire_date'], $data['status'], $id]);
    }

    public function delete(int $id): void
    {
        $statement = $this->pdo->prepare('DELETE FROM employees WHERE id = ?');
        $statement->execute([$id]);
    }

    public function employeeNoExists(string $employeeNo, ?int $exceptId = null): bool
    {
        $sql = 'SELECT id FROM employees WHERE employee_no = ?';
        $params = [$employeeNo];
        if ($exceptId) {
            $sql .= ' AND id <> ?';
            $params[] = $exceptId;
        }
        $statement = $this->pdo->prepare($sql . ' LIMIT 1');
        $statement->execute($params);
        return (bool) $statement->fetch();
    }

    public function statistics(): array
    {
        $total = (int) $this->pdo->query('SELECT COUNT(*) AS value FROM employees')->fetch()['value'];
        $active = (int) $this->pdo->query("SELECT COUNT(*) AS value FROM employees WHERE status = 'active'")->fetch()['value'];
        $departments = (int) $this->pdo->query('SELECT COUNT(DISTINCT department) AS value FROM employees')->fetch()['value'];
        return ['total' => $total, 'active' => $active, 'departments' => $departments];
    }
}
