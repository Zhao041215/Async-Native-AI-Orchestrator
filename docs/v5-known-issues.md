# V5 Known Issues

## 2026-05-08

- If the user uploads `V5_test01` as the web root instead of the inner `release/` contents, `/` and `/health` return 404 because the app entry lives under `release/`.
- The current release package must be treated as a deployable root bundle, or the deployment instructions must explicitly say to upload the contents of `release/`.
- This is a packaging/deployment-root issue, not a system runtime fix.
- The generated `nginx.sample.conf` used `location / { try_files ... }`; in Baota's pseudo-static textbox this can conflict with the generated vhost config or fail to take effect. Use rewrite-only rules there:
  `if (!-e $request_filename) { rewrite ^/(.*)$ /index.php last; }`.
