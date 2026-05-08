<?php
declare(strict_types=1);

final class EmployeeController
{
    private EmployeeRepository $employees;

    public function __construct()
    {
        $this->employees = new EmployeeRepository();
    }

    public function index(): void
    {
        $keyword = trim((string) ($_GET['q'] ?? ''));
        $page = max(1, (int) ($_GET['page'] ?? 1));
        try {
            $data = $this->employees->paginate($keyword, $page);
        } catch (Throwable $error) {
            $data = ['items' => [], 'total' => 0, 'page' => 1, 'pages' => 1];
        }
        $rows = '';
        foreach ($data['items'] as $employee) {
            $id = (int) $employee['id'];
            $name = htmlspecialchars((string) $employee['name'], ENT_QUOTES, 'UTF-8');
            $no = htmlspecialchars((string) $employee['employee_no'], ENT_QUOTES, 'UTF-8');
            $department = htmlspecialchars((string) $employee['department'], ENT_QUOTES, 'UTF-8');
            $position = htmlspecialchars((string) $employee['position'], ENT_QUOTES, 'UTF-8');
            $status = htmlspecialchars((string) $employee['status'], ENT_QUOTES, 'UTF-8');
            $csrf = Csrf::field();
            $actions = Auth::user() ? "<a href="/admin/employees/{$id}/edit">Edit</a><form action="/admin/employees/{$id}/delete" method="post" onsubmit="return confirm('Delete this employee?')">{$csrf}<button type="submit">Delete</button></form>" : '';
            $rows .= "<tr><td>{$no}</td><td>{$name}</td><td>{$department}</td><td>{$position}</td><td>{$status}</td><td class="actions-cell">{$actions}</td></tr>";
        }
        if ($rows === '') {
            $rows = '<tr><td colspan="6">No employees found.</td></tr>';
        }
        $q = htmlspecialchars($keyword, ENT_QUOTES, 'UTF-8');
        $create = Auth::user() ? '<a class="button" href="/admin/employees/create">New employee</a>' : '<a class="button" href="/admin/login">Admin login</a>';
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Employees</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <nav class="topbar"><a href="/">Home</a><a href="/employees">Employees</a><a href="/admin/dashboard">Dashboard</a></nav>
    <section class="panel">
      <div class="section-title"><h1>Employees</h1>{$create}</div>
      <form class="search" method="get" action="/employees">
        <input name="q" value="{$q}" placeholder="Search by name, number, department">
        <button type="submit">Search</button>
      </form>
      <table><thead><tr><th>No.</th><th>Name</th><th>Department</th><th>Position</th><th>Status</th><th>Actions</th></tr></thead><tbody>{$rows}</tbody></table>
    </section>
  </main>
</body>
</html>
HTML);
    }

    public function create(): void
    {
        Auth::requireAdmin();
        $this->form('Create employee', '/admin/employees', []);
    }

    public function store(): void
    {
        Auth::requireAdmin();
        Csrf::verify();
        [$data, $errors] = Validator::employee($_POST);
        if (!$errors && $this->employees->employeeNoExists($data['employee_no'])) {
            $errors['employee_no'] = 'Employee number already exists.';
        }
        if ($errors) {
            $this->form('Create employee', '/admin/employees', $data, $errors);
            return;
        }
        $this->employees->create($data);
        header('Location: /employees');
        exit;
    }

    public function edit(int $id): void
    {
        Auth::requireAdmin();
        $employee = $this->employees->find($id);
        if (!$employee) {
            Response::notFound('Employee not found.');
        }
        $this->form('Edit employee', "/admin/employees/{$id}", $employee);
    }

    public function update(int $id): void
    {
        Auth::requireAdmin();
        Csrf::verify();
        [$data, $errors] = Validator::employee($_POST);
        if (!$errors && $this->employees->employeeNoExists($data['employee_no'], $id)) {
            $errors['employee_no'] = 'Employee number already exists.';
        }
        if ($errors) {
            $this->form('Edit employee', "/admin/employees/{$id}", $data, $errors);
            return;
        }
        $this->employees->update($id, $data);
        header('Location: /employees');
        exit;
    }

    public function delete(int $id): void
    {
        Auth::requireAdmin();
        Csrf::verify();
        $this->employees->delete($id);
        header('Location: /employees');
        exit;
    }

    private function form(string $title, string $action, array $values, array $errors = []): void
    {
        $field = fn(string $key, string $default = '') => htmlspecialchars((string) ($values[$key] ?? $default), ENT_QUOTES, 'UTF-8');
        $error = fn(string $key) => isset($errors[$key]) ? '<small class="error">' . htmlspecialchars($errors[$key], ENT_QUOTES, 'UTF-8') . '</small>' : '';
        $csrf = Csrf::field();
        $gender = $field('gender', 'male');
        $status = $field('status', 'active');
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{$title}</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <nav class="topbar"><a href="/employees">Employees</a><a href="/admin/dashboard">Dashboard</a></nav>
    <form class="panel form-grid" action="{$action}" method="post">
      <h1>{$title}</h1>
      {$csrf}
      <label>Name<input name="name" value="{$field('name')}" required>{$error('name')}</label>
      <label>Employee no<input name="employee_no" value="{$field('employee_no')}" required>{$error('employee_no')}</label>
      <label>Gender<select name="gender"><option value="male">Male</option><option value="female">Female</option></select>{$error('gender')}</label>
      <label>Department<input name="department" value="{$field('department')}" required>{$error('department')}</label>
      <label>Position<input name="position" value="{$field('position')}" required>{$error('position')}</label>
      <label>Phone<input name="phone" value="{$field('phone')}" pattern="[0-9+\-\s]{6,30}">{$error('phone')}</label>
      <label>Email<input name="email" type="email" value="{$field('email')}">{$error('email')}</label>
      <label>Hire date<input name="hire_date" type="date" value="{$field('hire_date')}" required>{$error('hire_date')}</label>
      <label>Status<select name="status"><option value="active">Active</option><option value="inactive">Inactive</option></select>{$error('status')}</label>
      <button type="submit">Save</button>
    </form>
  </main>
  <script>
    document.querySelector('[name="gender"]').value = "{$gender}";
    document.querySelector('[name="status"]').value = "{$status}";
  </script>
</body>
</html>
HTML);
    }
}
