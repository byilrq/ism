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
