# ISM 资产管理系统

> 当前说明适用于现行 **仪器资产管理版**（已移除电缆模块）。  
> 本 README 按当前程序结构、`ism.sh`、`config.yaml`、备份/回收站和存储逻辑重新整理；早期 `/root/asset_manager`、`app.zip`、`ism.sql`、`mount.sh` 等说明不再作为当前安装依据。

## 1. 项目简介

ISM 是一套基于 Flask + MariaDB 的轻量资产管理系统，当前只保留：

- 主设备（仪器）管理
- 配件管理
- 货架/位置管理
- 条形码扫描、检索与盘点
- Excel 导入/导出
- 图片上传与查看
- 设备修改日志
- 90 天回收站
- 数据库 + 程序代码每日快照与 90 天轮转

当前版本不再提供电缆管理功能。旧数据库中已经存在的 `cable*` 历史表不会自动删除，但新程序不会读取、展示或写入这些表。

---

## 2. 当前架构

```text
浏览器 / 手机
      │
      ▼
    Nginx
  2083 / 443
      │
      ▼
Gunicorn（2 × sync worker）
127.0.0.1:5000
      │
      ▼
 Flask / SQLAlchemy
      │
      ├── MariaDB：业务数据
      │
      └── upload_folder：图片、回收站、导入日志、轮转备份
```

运行目录固定为：

```text
/root/ism
```

主要系统文件：

```text
/root/ism/
├── app/                  # Flask 主程序
├── backups/              # 本机最新数据库/代码恢复副本
├── venv/                 # Python 虚拟环境
├── config.yaml           # 数据库、上传/存储目录等配置
├── configure_media.py    # Nginx 图片/静态文件优化
├── init_db.py            # 数据库表结构增量初始化
├── ism.sh                # 安装与管理脚本
├── ism_backup.py         # 数据库+代码备份、轮转、回收站清理
├── requirements.txt
└── run.py
```

相关系统文件：

```text
/etc/systemd/system/ism.service
/etc/nginx/sites-available/ism.conf
/etc/nginx/sites-enabled/ism.conf
/etc/cron.d/ism_backup
/root/.ism_install.conf
/var/log/ism_backup.log
```

---

## 3. 系统要求

推荐并要求使用：

- **Debian 12 或更新版本**
- **Ubuntu 22.04 或更新版本**
- systemd
- root 权限
- 能正常使用 `apt`

安装脚本会安装/使用：

- MariaDB
- Nginx（机器已安装时不会重复安装）
- Python 3 / venv / pip
- Gunicorn
- Flask / SQLAlchemy / PyMySQL
- cron
- Tesseract OCR
- ACL、FUSE、davfs2 等运行依赖

运行 `ism.sh` 必须使用 root：

```bash
cd /root/ism
bash ism.sh
```

---

## 4. 新安装

当前版本使用**完整本地程序包安装**，`ism.sh` 不再从 GitHub 自动下载早期的 `app.zip`。

如果程序包来自仓库/发布包，先将完整目录解压，然后在该目录运行：

```bash
chmod +x ism.sh
bash ism.sh
```

推荐顺序：

```text
1 → 安装依赖
2 → 安装系统
4 → 设置存储路径（需要远端/挂载盘时）
5 → 建立每日自动备份 cron
```

### 新安装数据库

当前版本**不再依赖 `ism.sql` 初始化数据库**。

菜单 2 会：

1. 创建 MariaDB 数据库和数据库账号；
2. 按当前程序模型创建最新表结构；
3. 创建管理员账号；
4. 写入 `config.yaml`；
5. 创建 `ism.service`；
6. 配置 Nginx；
7. 启动 Gunicorn。

管理员用户名和密码在安装时输入。当前程序会把管理员配置写入 `/root/ism/config.yaml`，因此建议限制该文件权限，并定期更换登录密码。

如果是从旧系统迁移业务数据，不要使用旧 `ism.sql` 作为程序安装依赖；应使用数据库快照 `ism_latest.sql` 按本文“数据库恢复”章节恢复。

---

## 5. Web 端口与域名

内部应用端口：

```text
127.0.0.1:5000
```

默认外部 Nginx 端口：

```text
2083
```

默认访问方式：

```text
https://域名:2083
```

当前脚本还支持：

- `2083`：独立 HTTPS 端口；
- `443`：Nginx 直接提供 HTTPS；
- `8080`：供 xray fallback/分流模式使用。

证书标准目录：

```text
/etc/letsencrypt/live/<域名>/fullchain.pem
/etc/letsencrypt/live/<域名>/privkey.pem
```

如果证书已经存在，程序会优先复用。管理菜单 **7「更新域名」** 也支持通过 certbot 准备/更新 Let's Encrypt 证书。

---

## 6. config.yaml

核心配置示例：

```yaml
secret_key: <随机密钥>
mysql:
  host: localhost
  port: 3306
  database: ism
  user: asset_user
  password: <数据库密码>

upload_folder: /root/ism/app/uploads
max_content_length: 20971520

admin:
  username: <管理员用户名>
  password: <管理员密码>
```

### upload_folder 是唯一存储根目录

当前程序、图片访问、Nginx 图片优化以及轮转备份都以：

```yaml
upload_folder: <绝对路径>
```

作为统一存储根目录。

默认值：

```yaml
upload_folder: /root/ism/app/uploads
```

没有挂载盘、挂载暂不可用或首次安装时，可直接使用默认本地目录。

---

## 7. 自定义存储目录 / 云盘挂载

当前设计不再要求固定的 `/ism_images` 后缀，也不会自动在用户输入路径后追加目录名。

管理菜单：

```text
[4] 存储路径设置
    [1] 设置存储路径
    [2] 检测连通性
```

必须输入**最终使用的完整绝对路径**，例如：

```text
/mnt/rclone/ism
/mnt/CloudDrive/ism
/mnt/webdav_mount/ism
```

输入：

```text
/mnt/rclone/ism
```

最终 `config.yaml` 就会变成：

```yaml
upload_folder: /mnt/rclone/ism
```

不会变成 `/mnt/rclone/ism/ism_images`。

### 重要规则

自定义存储目录必须：

1. 是绝对路径；
2. 提前存在；
3. 已经挂载成功（如果是 rclone/WebDAV/CloudDrive）；
4. root 可写。

程序**不会替你创建最终挂载目录本身**。确认最终目录存在后，程序会在其中建立业务子目录。

典型结构：

```text
<upload_folder>/
├── assets/              # 主设备图片
├── accessories/         # 配件图片
├── asset_locations/     # 货架/位置图片
├── import_logs/         # Excel 导入失败日志
├── recycle/             # 回收站图片和恢复信息
│   └── records/
├── sql_backups/         # 90天数据库历史快照
└── code_backups/        # 90天程序代码历史快照
```

数据库本体始终运行在本机 MariaDB。切换 `upload_folder` 只影响图片、回收站文件、导入日志及轮转快照等文件型数据。

当前发布包不再包含独立的 `mount.sh`。如使用外部 rclone/WebDAV/CloudDrive 挂载，推荐先在系统层完成挂载，再通过菜单 4 把最终目录写入 `config.yaml`。

---

## 8. 图片功能

### 保存方式

图片保持原图，不主动压缩、缩放或降低质量。

每个主设备、配件或货架最多保留 5 张图片。

图片相对目录：

```text
<upload_folder>/assets/
<upload_folder>/accessories/
<upload_folder>/asset_locations/
```

数据库只保存相对路径，例如：

```text
assets/308189900202600014.2026.07.10.A1b2C3.jpg
```

因此 `upload_folder` 必须指向正确的图片根目录。

### 弱网重复上传保护

当前上传流程包含：

- 图片内容 SHA-256 去重；
- 同一表单重复提交保护；
- 上传成功但客户端未收到响应时的重试保护；
- 多 Gunicorn worker 并发防重复；
- 原有图片文件命名格式保持不变。

### 图片访问优化

程序优先使用 Nginx/系统能力发送图片；不满足 ACL/Nginx 直出条件时自动回退到 Flask，不会因为缺少 `setfacl` 而阻止系统运行。

---

## 9. 仪器与配件功能

当前首页只显示“仪器”，设备类型包括：

- 主设备
- 配件

主要字段包括：

- 集团编号
- 资产编号/内部编号
- 名称
- 型号
- 责任人
- 位置
- 状态
- 备注
- 时间
- 图片

只要设备发生有效修改并保存，设备“时间”字段自动刷新为服务器当天日期（`YYYY-MM-DD`）。

支持：

- 新增设备/配件
- 修改设备信息
- 盘点
- 删除/恢复
- Excel 批量导入
- Excel 导出
- 货架/位置维护
- 设备/配件图片管理

---

## 10. 检索规则

首页支持按：

- 编号
- 责任人
- 位置
- 备注
- 名称
- 型号

检索。

### 6 位编号

输入**恰好 6 位纯数字**时，进入严格“后 6 位”编号匹配模式，只按资产/配件编号的最后 6 位匹配，不混入普通文本模糊结果。

完整编号则按完整编号逻辑处理，不再无条件截取最后 6 位。

还可以按设备状态、主设备/配件类型进一步筛选。

---

## 11. 扫码与盘点

系统提供手机摄像头条形码扫描：

- 检索模式：扫描后直接查找资产；
- 盘点模式：扫描后更新设备盘点信息；
- 标签图片识别：可通过图片进行标签识别/辅助录入。

扫码页面针对手机端显示和摄像头使用进行了适配。

---

## 12. 设备修改日志

顶部 `📋` 图标进入设备修改日志。

页面显示最近 **50 条**主设备/配件修改记录，包括：

```text
时间
类型
集团编号
资产编号
资产名称
修改内容
```

集团编号和资产编号存在时均可点击，直接进入对应设备/配件详情。

日志覆盖：

- 盘点
- 集团编号修改
- 资产/内部编号修改
- 名称修改
- 型号修改
- 责任人修改
- 位置修改
- 状态修改
- 备注修改
- 图片上传
- 图片删除
- 新增
- 删除
- 恢复
- Excel 导入产生的修改

日志记录具体时间到秒；设备业务“时间”字段仍只保存到日。

---

## 13. 回收站（90 天可恢复）

删除主设备或配件不是立即物理删除。

删除时：

1. 数据库记录进入“已删除”状态；
2. `deleted_at` 记录删除时间；
3. 对应图片移动到：

```text
<upload_folder>/recycle/...
```

4. 恢复信息写入：

```text
<upload_folder>/recycle/records/
```

恢复时：

- 恢复数据库设备信息；
- 将图片从 `recycle` 移回原来的 `assets/` 或 `accessories/`；
- 删除对应回收记录。

删除主设备时，其当时仍有效的配件会一起进入回收站；恢复主设备时，只自动恢复随该主设备一同删除的配件，不会误恢复更早单独删除的配件。

### 90 天清理

回收站保留 **90 天**。

清理不是在打开回收站页面时执行，而是在每天自动快照**成功完成之后**执行。因此即将过期的数据会先进入当天数据库/代码快照，然后才永久清理。

---

## 14. 数据库 + 程序代码备份

### 本机最新副本

每次备份都会刷新：

```text
/root/ism/backups/ism_latest.sql
/root/ism/backups/ism_code_latest.tar.gz
```

其中：

- `ism_latest.sql` 用于菜单 6 快速恢复数据库；
- `ism_code_latest.tar.gz` 保存当前运行代码和配置；
- 代码备份不会包含 `venv`、上传图片、缓存和历史备份目录。

### 90 天历史轮转

同时按 `config.yaml -> upload_folder` 生成：

```text
<upload_folder>/sql_backups/ism_latest.YYYY.MM.DD.sql
<upload_folder>/code_backups/ism_code.YYYY.MM.DD.tar.gz
```

数据库历史快照和程序代码历史快照均保留 **90 天**，超过 90 天自动删除。

### 每日自动时间

cron 默认：

```cron
0 22 * * *
```

即每天 **22:00（服务器本地时间）**。

实际 cron 使用 ISM 虚拟环境 Python：

```text
/root/ism/venv/bin/python /root/ism/ism_backup.py
```

并使用 `flock` 防止重复并发执行。

### 每日执行顺序

```text
22:00
  ↓
生成本机最新数据库快照
  ↓
生成本机最新程序代码快照
  ↓
同步当日数据库历史快照到 upload_folder/sql_backups
  ↓
同步当日代码历史快照到 upload_folder/code_backups
  ↓
清理超过90天的数据库历史快照
  ↓
清理超过90天的代码历史快照
  ↓
清理超过90天的回收站数据和图片
```

如果 `upload_folder` 不存在或不可写，本机最新快照可能已经生成，但轮转同步会被判为失败，回收站自动清理也不会继续执行，以避免在存储异常时继续删除历史数据。

### 首次开启自动备份

安装完成后进入：

```text
菜单 5 → 1 生成/重置 cron 备份任务
```

查看状态：

```text
菜单 5 → 2
```

手动立即备份：

```text
菜单 5 → 4
```

日志：

```text
/var/log/ism_backup.log
```

---

## 15. 数据库恢复

恢复文件固定使用：

```text
/root/ism/backups/ism_latest.sql
```

把需要恢复的 SQL 放到这个路径，然后运行：

```text
ism.sh → 菜单 6 → 输入 YES
```

完整流程：

```text
停止 ISM
  ↓
删除并重新创建 ism 数据库
  ↓
导入 ism_latest.sql
  ↓
执行 init_db.py 补齐当前版本新增表结构
  ↓
全部成功后启动 ISM
```

如果导入或表结构补齐失败，ISM 会保持停止状态，不会让空库/半恢复数据库继续运行。

### 注意

SQL 备份保存数据库记录，不包含 JPEG/PNG 文件本体。

如果恢复到另一台服务器，还需要同时保证 `upload_folder` 中的图片目录存在，或者从对应存储备份恢复图片文件。

---

## 16. 管理菜单

当前 `ism.sh` 主菜单：

```text
[1] 安装依赖
[2] 安装系统
[3] 重启系统
[4] 存储路径设置
[5] 数据+代码备份（cron）
[6] 恢复数据库
[7] 更新域名
[8] 卸载系统
[0] 退出
```

### 菜单 3：重启系统

重启前会先：

- 执行 `init_db.py`，增量补齐新表；
- 检查/迁移旧备份 cron 的 Python 路径；
- 自动把旧的 20:00 cron 调整为 22:00；
- 应用图片/Nginx 配置；
- 重启 `ism.service`。

因此手动覆盖新版程序文件后，应执行一次：

```text
菜单 3 → 重启系统
```

### 菜单 4：存储路径

```text
1 = 设置完整存储目录
2 = 检测连通性
```

### 菜单 5：备份

```text
1 = 生成/重置 cron 备份任务
2 = 查看 cron 任务和运行情况
3 = 删除 cron 备份任务
4 = 手动备份数据库 + 程序代码
0 = 返回主菜单
```

---

## 17. 手动升级现有系统

当前发布包不再提供独立 `upgrade.sh`。

现有系统升级建议：

1. 先执行菜单 `5 → 4`，手动生成数据库 + 代码备份；
2. 保留服务器当前 `/root/ism/config.yaml`；
3. 如果图片使用本地 `/root/ism/app/uploads`，绝对不要在覆盖代码时删除该目录；
4. 用新版运行文件覆盖对应程序文件；
5. 不要执行菜单 2 作为普通升级方式；
6. 运行：

```bash
cd /root/ism
bash ism.sh
```

7. 选择菜单 `3` 重启系统。

菜单 3 会执行增量数据库初始化，不会主动清空现有业务数据。

---

## 18. 常用检查命令

### ISM 服务

```bash
systemctl status ism --no-pager
journalctl -u ism -n 100 --no-pager
```

### Nginx

```bash
nginx -t
systemctl status nginx --no-pager
```

### cron

```bash
cat /etc/cron.d/ism_backup
systemctl status cron --no-pager
```

### 备份日志

```bash
tail -n 100 /var/log/ism_backup.log
```

### 当前存储目录

```bash
grep '^upload_folder:' /root/ism/config.yaml
```

### 手动执行一次备份

推荐从：

```text
ism.sh → 菜单 5 → 4
```

执行，以便同时检查 Python 环境、数据库快照、代码快照和轮转同步结果。

---

## 19. 与早期 README 的主要差异

| 早期设计 | 当前设计 |
| --- | --- |
| `/root/asset_manager` | `/root/ism` |
| 从 GitHub `app.zip` 下载应用 | 完整本地发布包直接安装 |
| `ism.sql` 初始化数据库和用户 | `init_db.py` 按当前模型建表，管理员安装时创建 |
| 独立 `mount.sh` | 当前发布包不再依赖 `mount.sh`，优先外部挂载后设置完整 `upload_folder` |
| 输入挂载点后自动追加 `/ism_images` | 必须输入完整最终目录，不自动追加任何路径 |
| 图片固定在 `/root/asset_manager/app/uploads/images/...` | 图片根目录统一由 `config.yaml -> upload_folder` 决定 |
| 只备份数据库 | 数据库 + 当前程序代码同时备份 |
| 保留一份最新备份 | 本机保留最新副本 + `upload_folder` 中保留90天日期快照 |
| 备份时间 20:00 | 每天 22:00 |
| 回收站 30天 | 90天，并且设备信息与图片共同恢复 |
| 仪器 + 电缆 | 当前仅保留仪器 + 配件 |
| 旧单体/早期拆分版本说明 | 当前按现行模块化运行结构维护，不再以旧 1.0/2.0 说明为安装依据 |

---

## 20. 数据安全原则

当前系统把数据分成三类：

### MariaDB

保存：

- 主设备信息
- 配件信息
- 图片相对路径
- 设备状态
- 修改日志
- 回收状态等结构化数据

### upload_folder

保存：

- 原始图片文件
- 货架图片
- 回收站文件
- 导入日志
- 90天数据库快照
- 90天代码快照

### /root/ism/backups

保存：

- 当前最新数据库快照
- 当前最新代码快照

因此完整灾难恢复至少需要：

```text
数据库 SQL 快照
+
upload_folder 文件数据
+
程序代码/发布包
```

不要把 `ism_latest.sql` 理解成完整图片备份；SQL 中只有图片路径记录，没有图片二进制数据。

---

## 21. 当前维护建议

正常运行后建议定期确认：

```text
1. config.yaml 中 upload_folder 指向正确且可写的绝对路径
2. 菜单 5 → 2 显示 cron 正常
3. 每天 22:00 后 /var/log/ism_backup.log 有成功记录
4. sql_backups/ 和 code_backups/ 持续产生日期快照
5. 远端挂载不可用时先恢复挂载，不要把同名普通本地目录误当成远端目录
6. 升级前先执行一次菜单 5 → 4 手动备份
```

---

**项目状态：当前版本为仪器资产管理系统，不包含电缆模块。**
