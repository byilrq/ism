1.  /root/asset_manager/app  位于 https://github.com/byilrq/ism/blob/main/app.zip
2. https://github.com/byilrq/ism/blob/main/asset_manager.sql   
3. 如果需要添加域名：可以添加域名，域名对应的证书在/etc/letsencrypt/live下面
4. 反向代理的端口是2083，最后可以使用域名：2083访问。
5. 每天自动备份数据库到/root/asset_manager/backups和云盘,滚动保留一份最新的数据库备份删除旧的备份文件。
6. 系统必须是debian 12或者ununtu 22以上系统。
7. 远端路径配置必须提前创建和带上/asset_manager 文件夹，系统不创建这个文件夹。比如/mnt/webdav_mount/ism_images或/mnt/CloudDrive/ism_images
8. 主设备图片上传位置：/root/asset_manager/app/uploads/images/assets
9. 配件图片上传位置：/root/asset_manager/app/uploads/images/accessories
   


