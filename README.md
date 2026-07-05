1.  /root/asset_manager/app  位于 https://github.com/byilrq/ism/blob/main/app.zip
2. 如果需要添加域名：可以添加域名，域名对应的证书在/etc/letsencrypt/live下面
3. 反向代理的端口是2083，最后可以使用域名：2083访问。
4. 每天自动备份数据库到/root/asset_manager/backups和云盘,滚动保留一份最新的数据库备份删除旧的备份文件。
5. 系统必须是debian 12或者ununtu 22以上系统。
6. 远端路径配置必须提前创建和带上/asset_manager 文件夹，系统不创建这个文件夹。比如/mnt/webdav_mount/ism_images或/mnt/CloudDrive/ism_images
7. 主设备图片上传位置：/root/asset_manager/app/uploads/images/assets
8. 配件图片上传位置：/root/asset_manager/app/uploads/images/accessories
9. 数据库按照仓库的 ism.sql   初始化初步数据和用户名。
10. 切换到 WebDAV 或 CloudDrive，脚本会把 config.py 里的 UPLOAD_FOLDER 改成对应挂载目录；但数据库本体仍然在本机 MariaDB，只是备份和图片会按你选择的存储方式同步/写入。
11. 1.0版本是 架构没有拆分前的完全版本，2.0是架构拆分后的。
   


