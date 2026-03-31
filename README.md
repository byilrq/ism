1.  /root/asset_manager/app  位于 https://github.com/byilrq/ism/blob/main/app.rar
2.  /root/asset_manager/config.py  位于 https://github.com/byilrq/ism/blob/main/config.py
3.  /root/asset_manager/run.py 位于 https://github.com/byilrq/ism/blob/main/run.py
4. /var/lib/mysql/asset_manager 位于 https://github.com/byilrq/ism/blob/main/asset_manager.rar
5. https://github.com/byilrq/ism/blob/main/asset_manager.sql 

6. 如果需要添加域名：可以添加域名，域名对应的证书在/etc/letsencrypt/live下面
7. 反向代理的端口是2083，最后可以使用域名：2083访问。
8. 菜单项有1.安装 2.重启 3.添加每天自动备份数据库到/root/asset_manager/backups，滚动保留一份最新的数据库备份删除旧的备份文件；
9. 重启就是如果更新了更新完程序后重启：
systemctl daemon-reload
systemctl enable asset_manager
systemctl restart asset_manage
systemctl status asset_manager
主设备图片上传位置：/root/asset_manager/app/uploads/images/assets
配件图片上传位置：/root/asset_manager/app/uploads/images/accessories


ism.sh 使用说明

1. 上传到服务器：
   chmod +x ism.sh

2. 以 root 运行：
   sudo ./ism.sh

3. 菜单项：
   1) 安装
   2) 重启
   3) 添加每天自动备份数据库

4. 安装流程会自动：
   - 安装 nginx / mariadb / python3 / cron / unar
   - 下载 app.rar、config.py、run.py、requirements.txt、asset_manager.sql
   - 创建 /root/asset_manager 目录
   - 解压 app 到 /root/asset_manager/app
   - 安装 Python 依赖
   - 创建数据库 asset_manager 并导入 asset_manager.sql
   - 写入 systemd 服务 asset_manager
   - 配置 nginx 监听 2083
   - 如检测到 /etc/letsencrypt/live/<域名>/ 下证书，则启用 https://域名:2083

5. 重启菜单会：
   - 重新下载程序文件
   - 覆盖 app、config.py、run.py、requirements.txt
   - 重新安装 requirements
   - 执行：
     systemctl daemon-reload
     systemctl enable asset_manager
     systemctl restart asset_manager
     systemctl status asset_manager

6. 自动备份菜单会：
   - 每天 02:00 备份数据库到 /root/asset_manager/backups
   - 仅保留最新一份：asset_manager_daily_latest.sql

说明：
- 你之前发的 restart 命令里有一个小拼写问题：asset_manage
  脚本中已修正为 asset_manager。

