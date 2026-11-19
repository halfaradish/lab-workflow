# 可选的 systemd 定时清理

这两个文件是**样例**，仓库不会自动安装或启用它们。默认路径假设 lab 仓库位于
`~/project/lab-workflow`；如果你的路径不同，请同时修改 service 中的
`WorkingDirectory` 和 `ExecStart`。

启用前先用演练模式确认会删什么：

```bash
cd ~/project/lab-workflow
./bin/lab-workflow clean
```

手工安装为当前用户的 systemd timer：

```bash
mkdir -p ~/.config/systemd/user
cp contrib/systemd/lab-workflow-clean.service contrib/systemd/lab-workflow-clean.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lab-workflow-clean.timer
systemctl --user list-timers lab-workflow-clean.timer
```

查看本次执行：

```bash
systemctl --user status lab-workflow-clean.service
journalctl --user -u lab-workflow-clean.service -n 100 --no-pager
```

停用：

```bash
systemctl --user disable --now lab-workflow-clean.timer
rm ~/.config/systemd/user/lab-workflow-clean.{service,timer}
systemctl --user daemon-reload
```

注意：

- `clean --apply` 会真正删除过期实验，但仍保留 registry 墓碑。
- 非 Git 或有未提交更改的实验默认跳过，不会因为定时器而 `--force`。
- timer 使用 `Persistent=true`，关机期间错过的执行在下次启动后补一次。
- 这是用户级 timer，不需要 root，也不会修改系统级配置。
