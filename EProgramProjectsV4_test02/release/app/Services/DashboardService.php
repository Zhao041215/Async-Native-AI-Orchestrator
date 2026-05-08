<?php
declare(strict_types=1);

final class DashboardService
{
    public function __construct(private ?EmployeeRepository $employees = null)
    {
        $this->employees = $employees ?: new EmployeeRepository();
    }

    public function cards(): array
    {
        $stats = $this->employees->statistics();
        return [
            ['label' => 'Total employees', 'value' => $stats['total']],
            ['label' => 'Active employees', 'value' => $stats['active']],
            ['label' => 'Departments', 'value' => $stats['departments']],
        ];
    }
}
