/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19  Distrib 10.11.14-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: asset_manager
-- ------------------------------------------------------
-- Server version	10.11.14-MariaDB-0+deb12u2

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `accessories`
--

DROP TABLE IF EXISTS `accessories`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `accessories` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `parent_asset_id` int(11) DEFAULT NULL COMMENT '所属主设备ID，可空',
  `sub_group_no` varchar(64) DEFAULT NULL COMMENT '附属资产集团编号',
  `sub_internal_no` varchar(64) DEFAULT NULL COMMENT '附属资产内部编号',
  `name` varchar(255) NOT NULL COMMENT '名称',
  `model` varchar(255) DEFAULT NULL COMMENT '型号',
  `owner` varchar(100) DEFAULT NULL COMMENT '责任人',
  `location` varchar(100) DEFAULT NULL COMMENT '位置',
  `asset_date` date DEFAULT NULL COMMENT '时间',
  `status` varchar(50) DEFAULT NULL COMMENT '状态',
  `remark` text DEFAULT NULL COMMENT '备注',
  `image_path` varchar(500) DEFAULT NULL COMMENT '图片路径',
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `sub_group_no` (`sub_group_no`),
  UNIQUE KEY `sub_internal_no` (`sub_internal_no`),
  KEY `idx_accessories_parent_asset_id` (`parent_asset_id`),
  KEY `idx_accessories_sub_group_no` (`sub_group_no`),
  KEY `idx_accessories_sub_internal_no` (`sub_internal_no`),
  CONSTRAINT `fk_accessories_asset` FOREIGN KEY (`parent_asset_id`) REFERENCES `assets` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `accessories`
--

LOCK TABLES `accessories` WRITE;
/*!40000 ALTER TABLE `accessories` DISABLE KEYS */;
INSERT INTO `accessories` VALUES
(1,3,'308651411001001-001','651411001001-001','11','22','33','44','2026-03-31','在库','','accessories/9eac47a3ec034d04be8ffaeeb88a981b.jpg','2026-03-31 11:23:24','2026-03-31 13:32:59'),
(6,3,NULL,'651411001001-002','目标模拟器','大华','小王','设计楼321','2026-03-31','在库','',NULL,'2026-03-31 19:14:24','2026-03-31 19:14:24'),
(7,NULL,NULL,'651411001001-003','相机','尼康','小白','华宁路','2026-03-31','维修','',NULL,'2026-03-31 19:15:01','2026-03-31 19:15:01'),
(8,3,NULL,'651411001001-006','电表','11','小白','元江路321','2026-03-31','维修','',NULL,'2026-03-31 19:35:38','2026-03-31 19:35:38');
/*!40000 ALTER TABLE `accessories` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `accessory_images`
--

DROP TABLE IF EXISTS `accessory_images`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `accessory_images` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `accessory_id` int(11) NOT NULL,
  `image_path` varchar(500) NOT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_accessory_images_accessory_id` (`accessory_id`),
  CONSTRAINT `fk_accessory_images_accessory` FOREIGN KEY (`accessory_id`) REFERENCES `accessories` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `accessory_images`
--

LOCK TABLES `accessory_images` WRITE;
/*!40000 ALTER TABLE `accessory_images` DISABLE KEYS */;
/*!40000 ALTER TABLE `accessory_images` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `asset_images`
--

DROP TABLE IF EXISTS `asset_images`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `asset_images` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asset_id` int(11) NOT NULL,
  `image_path` varchar(500) NOT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_asset_images_asset_id` (`asset_id`),
  CONSTRAINT `fk_asset_images_asset` FOREIGN KEY (`asset_id`) REFERENCES `assets` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `asset_images`
--

LOCK TABLES `asset_images` WRITE;
/*!40000 ALTER TABLE `asset_images` DISABLE KEYS */;
/*!40000 ALTER TABLE `asset_images` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `assets`
--

DROP TABLE IF EXISTS `assets`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `assets` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `group_no` varchar(64) DEFAULT NULL COMMENT '集团编号',
  `internal_no` varchar(64) DEFAULT NULL COMMENT '内部编号',
  `name` varchar(255) NOT NULL COMMENT '名称',
  `model` varchar(255) DEFAULT NULL COMMENT '型号',
  `owner` varchar(100) DEFAULT NULL COMMENT '责任人',
  `location` varchar(100) DEFAULT NULL COMMENT '位置',
  `asset_date` date DEFAULT NULL COMMENT '时间',
  `status` varchar(50) DEFAULT NULL COMMENT '状态',
  `remark` text DEFAULT NULL COMMENT '备注',
  `image_path` varchar(500) DEFAULT NULL COMMENT '图片路径',
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `group_no` (`group_no`),
  UNIQUE KEY `internal_no` (`internal_no`),
  KEY `idx_assets_group_no` (`group_no`),
  KEY `idx_assets_internal_no` (`internal_no`)
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `assets`
--

LOCK TABLES `assets` WRITE;
/*!40000 ALTER TABLE `assets` DISABLE KEYS */;
INSERT INTO `assets` VALUES
(3,'308651411001001','651411001001','目标模拟器','ht','小王','元江路','2026-03-31','借出','带了好几个散件','assets/33d0394ef70d435e9849aa485f7b529a.png','2026-03-31 10:59:24','2026-03-31 21:50:23'),
(4,'88888888898','9898988','888888','6666666','静茹','509-1234-5098','2026-03-31','在库','20260331','assets/87e20b2f94844694ae28abf43e911e9f.jpg','2026-03-31 12:25:29','2026-03-31 12:25:29');
/*!40000 ALTER TABLE `assets` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `dict_options`
--

DROP TABLE IF EXISTS `dict_options`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `dict_options` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `dict_type` varchar(50) NOT NULL COMMENT '字典类型，如 status/location/owner',
  `dict_value` varchar(100) NOT NULL COMMENT '选项值',
  `sort_order` int(11) DEFAULT 0 COMMENT '排序',
  `is_active` tinyint(1) DEFAULT 1 COMMENT '是否启用',
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dict_type_value` (`dict_type`,`dict_value`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `dict_options`
--

LOCK TABLES `dict_options` WRITE;
/*!40000 ALTER TABLE `dict_options` DISABLE KEYS */;
INSERT INTO `dict_options` VALUES
(1,'status','在库',1,1,'2026-03-30 18:30:13'),
(2,'status','借出',2,1,'2026-03-30 18:30:13'),
(3,'status','维修',3,1,'2026-03-30 18:30:13'),
(4,'status','报废',4,1,'2026-03-30 18:30:13');
/*!40000 ALTER TABLE `dict_options` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `username` varchar(100) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--

LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
INSERT INTO `users` VALUES
(1,'admin','scrypt:32768:8:1$UkFXfKUf1bN7W6AH$1171dad9c03182ef6ca00e4e58084d50c1ce5b0cf8462ccaca80c1ed34bbc251dd8c6343feb3cdbb7f02f721fc61cdcffca4444a2d6b9760a46a8ad7eeabaff9','2026-03-30 19:45:27');
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-03-31 22:19:03
