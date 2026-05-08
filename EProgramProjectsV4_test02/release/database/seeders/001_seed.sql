INSERT INTO admin_users (username, password_hash, display_name, active)
VALUES ('admin', '$2y$10$X8T4mxCZh7htMZVrL81a4.yT2Rwjd7s1N46T0jnA.F6pV3OyHq/2S', 'System Administrator', 1)
ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), active = VALUES(active);
