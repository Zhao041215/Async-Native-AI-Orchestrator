<?php
declare(strict_types=1);

final class Validator
{
    public static function employee(array $input): array
    {
        $errors = [];
        $data = [
            'name' => trim((string) ($input['name'] ?? '')),
            'employee_no' => strtoupper(trim((string) ($input['employee_no'] ?? ''))),
            'gender' => trim((string) ($input['gender'] ?? '')),
            'department' => trim((string) ($input['department'] ?? '')),
            'position' => trim((string) ($input['position'] ?? '')),
            'phone' => trim((string) ($input['phone'] ?? '')),
            'email' => trim((string) ($input['email'] ?? '')),
            'hire_date' => trim((string) ($input['hire_date'] ?? '')),
            'status' => trim((string) ($input['status'] ?? 'active')),
        ];
        foreach (['name', 'employee_no', 'department', 'position', 'hire_date'] as $field) {
            if ($data[$field] === '') {
                $errors[$field] = 'This field is required.';
            }
        }
        if (!in_array($data['gender'], ['male', 'female'], true)) {
            $errors['gender'] = 'Choose a valid gender.';
        }
        if (!in_array($data['status'], ['active', 'inactive'], true)) {
            $errors['status'] = 'Choose a valid status.';
        }
        if ($data['phone'] !== '' && !preg_match('/^[0-9+\-\s]{6,30}$/', $data['phone'])) {
            $errors['phone'] = 'Phone format is invalid.';
        }
        if ($data['email'] !== '' && !filter_var($data['email'], FILTER_VALIDATE_EMAIL)) {
            $errors['email'] = 'Email format is invalid.';
        }
        if ($data['hire_date'] !== '' && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $data['hire_date'])) {
            $errors['hire_date'] = 'Use YYYY-MM-DD.';
        }
        return [$data, $errors];
    }
}
