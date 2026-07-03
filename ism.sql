/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19  Distrib 10.11.14-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: ism
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
) ENGINE=InnoDB AUTO_INCREMENT=12 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `accessories`
--

LOCK TABLES `accessories` WRITE;
/*!40000 ALTER TABLE `accessories` DISABLE KEYS */;
INSERT INTO `accessories` VALUES
(11,8,'308090300202000027-001','651411041008-001','测试设备','','','元江路-348货架','2026-04-10','','',NULL,'2026-04-10 19:05:32','2026-04-10 19:05:32');
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
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
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
) ENGINE=InnoDB AUTO_INCREMENT=60 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `asset_images`
--

LOCK TABLES `asset_images` WRITE;
/*!40000 ALTER TABLE `asset_images` DISABLE KEYS */;
INSERT INTO `asset_images` VALUES
(52,8,'assets/308090300202000027.2026.04.10.b560bd2900a7470c.jpg','2026-04-10 11:00:57'),
(53,8,'assets/308090300202000027.2026.04.10.b6a297c55008473c.jpg','2026-04-10 11:00:57'),
(54,8,'assets/308090300202000027.2026.04.10.9b61d8c98b2143ff.jpg','2026-04-10 11:00:57'),
(55,9,'assets/0001.2026.04.10.7f510e6fbd8a40f7.jpg','2026-04-10 11:02:31'),
(56,9,'assets/0001.2026.04.10.ee97cedf58a940f1.jpg','2026-04-10 11:02:31'),
(57,9,'assets/0001.2026.04.10.f011e5fe47064123.jpg','2026-04-10 11:02:31'),
(58,9,'assets/0001.2026.04.10.8df7b22e5cd54d8d.jpg','2026-04-10 11:02:31');
/*!40000 ALTER TABLE `asset_images` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `asset_location_image`
--

DROP TABLE IF EXISTS `asset_location_image`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `asset_location_image` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `location_name` varchar(255) NOT NULL,
  `image_path` varchar(500) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_asset_location_image_location_name` (`location_name`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `asset_location_image`
--

LOCK TABLES `asset_location_image` WRITE;
/*!40000 ALTER TABLE `asset_location_image` DISABLE KEYS */;
INSERT INTO `asset_location_image` VALUES
(1,'元江路-348货架','asset_locations/asset.2026.04.10.b09a24df3b8c45a5.jpg','2026-04-10 04:36:39');
/*!40000 ALTER TABLE `asset_location_image` ENABLE KEYS */;
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
) ENGINE=InnoDB AUTO_INCREMENT=14 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `assets`
--

LOCK TABLES `assets` WRITE;
/*!40000 ALTER TABLE `assets` DISABLE KEYS */;
INSERT INTO `assets` VALUES
(8,'308090300202000027','651411041008','目标模拟器','SC','闲','元江路-348货架','2026-04-11','在库','',NULL,'2026-04-06 07:03:53','2026-04-11 07:27:47'),
(9,NULL,'0001','载荷程序高速上注及大回路比对子系统','f4','李天泽','元江路-348货架','2026-04-10','借出','两个箱子',NULL,'2026-04-07 14:10:50','2026-04-10 19:23:09');
/*!40000 ALTER TABLE `assets` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `cable`
--

DROP TABLE IF EXISTS `cable`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `cable` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cable_no` varchar(128) NOT NULL,
  `name` varchar(255) NOT NULL,
  `spec` varchar(32) DEFAULT NULL,
  `owner` varchar(128) DEFAULT NULL,
  `location` varchar(255) DEFAULT NULL,
  `status` varchar(32) DEFAULT NULL,
  `remark` text DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_cable_cable_no` (`cable_no`),
  KEY `ix_cable_owner` (`owner`),
  KEY `ix_cable_status` (`status`)
) ENGINE=InnoDB AUTO_INCREMENT=255 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `cable`
--

LOCK TABLES `cable` WRITE;
/*!40000 ALTER TABLE `cable` DISABLE KEYS */;
INSERT INTO `cable` VALUES
(4,'DMDL162243','','高频',NULL,'YAE-B4','在库',NULL,'2026-04-09 05:05:14','2026-04-09 05:05:14'),
(5,'DMDL210023','','高频',NULL,'YAF-B4','在库','不合格','2026-04-10 07:25:09','2026-04-10 07:25:15');
/*!40000 ALTER TABLE `cable` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `cable_image`
--

DROP TABLE IF EXISTS `cable_image`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `cable_image` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cable_id` int(11) NOT NULL,
  `image_path` varchar(500) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_cable_image_cable_id` (`cable_id`),
  CONSTRAINT `cable_image_ibfk_1` FOREIGN KEY (`cable_id`) REFERENCES `cable` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `cable_image`
--

LOCK TABLES `cable_image` WRITE;
/*!40000 ALTER TABLE `cable_image` DISABLE KEYS */;
INSERT INTO `cable_image` VALUES
(4,10,'cable/DMDL162500.2026.04.09.bf7db78a27674b16.jpg','2026-04-09 05:12:08');
/*!40000 ALTER TABLE `cable_image` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `cable_shelf`
--

DROP TABLE IF EXISTS `cable_shelf`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `cable_shelf` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `shelf_name` varchar(255) NOT NULL,
  `remark` text DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_cable_shelf_shelf_name` (`shelf_name`)
) ENGINE=InnoDB AUTO_INCREMENT=38 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `cable_shelf`
--

LOCK TABLES `cable_shelf` WRITE;
/*!40000 ALTER TABLE `cable_shelf` DISABLE KEYS */;
INSERT INTO `cable_shelf` VALUES
(4,'YAE-B4',NULL,'2026-04-09 05:04:40','2026-04-09 05:04:40'),
(5,'YAE-B5',NULL,'2026-04-09 05:09:40','2026-04-09 05:09:40'),
(6,'YAE-C3',NULL,'2026-04-09 05:19:16','2026-04-09 05:19:16'),
(7,'YAE-C1',NULL,'2026-04-09 05:28:28','2026-04-09 05:28:28'),
(8,'YAF-C5',NULL,'2026-04-09 05:37:25','2026-04-09 05:37:25'),
(9,'YAF-C4',NULL,'2026-04-09 05:40:12','2026-04-09 05:40:12'),
(10,'YAE-B2',NULL,'2026-04-09 06:17:28','2026-04-09 06:17:52'),
(11,'YAE-A3',NULL,'2026-04-09 06:37:15','2026-04-09 06:37:15'),
(12,'YAF-B5',NULL,'2026-04-09 06:47:01','2026-04-09 06:47:01'),
(13,'YAF-B4',NULL,'2026-04-09 06:56:29','2026-04-09 06:56:29'),
(14,'YAF-C2',NULL,'2026-04-09 07:03:54','2026-04-09 07:03:54'),
(15,'YAF-C1',NULL,'2026-04-09 07:19:09','2026-04-09 07:19:09'),
(16,'DLA-A1',NULL,'2026-04-09 07:45:57','2026-04-09 07:45:57'),
(17,'DLA-A2',NULL,'2026-04-09 07:56:06','2026-04-09 07:56:06'),
(32,'DLA-A3',NULL,'2026-04-10 04:55:48','2026-04-10 04:55:48'),
(33,'DLA-A4',NULL,'2026-04-10 05:06:03','2026-04-10 05:06:03'),
(34,'YAF-B1',NULL,'2026-04-10 05:43:41','2026-04-10 05:43:41'),
(35,'YAF-B2',NULL,'2026-04-10 05:52:55','2026-04-10 05:52:55'),
(36,'YAF-A1',NULL,'2026-04-10 06:03:08','2026-04-10 06:03:08'),
(37,'YAE-B1',NULL,'2026-04-10 07:16:22','2026-04-10 07:16:22');
/*!40000 ALTER TABLE `cable_shelf` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `cable_shelf_image`
--

DROP TABLE IF EXISTS `cable_shelf_image`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `cable_shelf_image` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `shelf_id` int(11) NOT NULL,
  `image_path` varchar(500) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_cable_shelf_image_shelf_id` (`shelf_id`),
  CONSTRAINT `cable_shelf_image_ibfk_1` FOREIGN KEY (`shelf_id`) REFERENCES `cable_shelf` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=41 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `cable_shelf_image`
--

LOCK TABLES `cable_shelf_image` WRITE;
/*!40000 ALTER TABLE `cable_shelf_image` DISABLE KEYS */;
INSERT INTO `cable_shelf_image` VALUES
(17,17,'cable_shelf/DLA-A2.2026.04.10.e5b38c53ac5947e8.jpg','2026-04-10 04:53:41'),
(18,17,'cable_shelf/DLA-A2.2026.04.10.2a07720e25694985.jpg','2026-04-10 04:53:58'),
(19,32,'cable_shelf/DLA-A3.2026.04.10.2e1c1492c9694a05.jpg','2026-04-10 04:59:39'),
(20,33,'cable_shelf/DLA-A4.2026.04.10.43b87e2156f74c18.jpg','2026-04-10 05:15:26'),
(21,33,'cable_shelf/DLA-A4.2026.04.10.92a017e927c54f23.jpg','2026-04-10 05:15:43'),
(22,16,'cable_shelf/DLA-A1.2026.04.10.95e4c490f8884cf2.jpg','2026-04-10 05:20:18'),
(23,11,'cable_shelf/YAE-A3.2026.04.10.9759109d3a9e45b0.jpg','2026-04-10 05:21:40'),
(24,10,'cable_shelf/YAE-B2.2026.04.10.24fbe3bb50034bf4.jpg','2026-04-10 05:22:26'),
(25,4,'cable_shelf/YAE-B4.2026.04.10.72a73ee2d6e64a49.jpg','2026-04-10 05:22:59'),
(26,5,'cable_shelf/YAE-B5.2026.04.10.29591b1ec8994dc0.jpg','2026-04-10 05:23:20'),
(27,7,'cable_shelf/YAE-C1.2026.04.10.9293fcd1a7844d1a.jpg','2026-04-10 05:23:47'),
(28,6,'cable_shelf/YAE-C3.2026.04.10.a36df61be7214d66.jpg','2026-04-10 05:24:09'),
(29,15,'cable_shelf/YAF-C1.2026.04.10.75d5c9a9ab9b4abb.jpg','2026-04-10 05:24:56'),
(31,9,'cable_shelf/YAF-C4.2026.04.10.d877d36b2e3b4bcf.jpg','2026-04-10 05:25:40'),
(32,8,'cable_shelf/YAF-C5.2026.04.10.ee10f0f7c81341a5.jpg','2026-04-10 05:26:10'),
(33,34,'cable_shelf/YAF-B1.2026.04.10.e324e7edc54d4681.jpg','2026-04-10 05:44:06'),
(34,36,'cable_shelf/YAF-A1.2026.04.10.9142a51c08fe4bc7.jpg','2026-04-10 06:21:35'),
(35,36,'cable_shelf/YAF-A1.2026.04.10.c8260aeaad13459b.jpg','2026-04-10 06:21:48'),
(36,14,'cable_shelf/YAF-C2.2026.04.10.3dc643a5d1ec4f53.jpg','2026-04-10 06:49:03'),
(37,35,'cable_shelf/YAF-B2.2026.04.10.1cae9b966acb4e35.jpg','2026-04-10 07:09:10'),
(38,13,'cable_shelf/YAF-B4.2026.04.10.040fde448d414e9b.jpg','2026-04-10 07:09:40'),
(39,12,'cable_shelf/YAF-B5.2026.04.10.738ab8ed570f4b97.jpg','2026-04-10 07:11:57'),
(40,37,'cable_shelf/YAE-B1.2026.04.10.4a439fb019c84949.jpg','2026-04-10 07:16:41');
/*!40000 ALTER TABLE `cable_shelf_image` ENABLE KEYS */;
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
(3,'status','报废',3,1,'2026-03-30 18:30:13'),
(4,'status','开箱',5,1,'2026-06-28 12:00:00'),
(5,'status','其它',6,1,'2026-06-28 12:00:00');
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
  `password` varchar(255) NOT NULL,
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
(1,'admin','Plex0819$','2026-03-30 19:45:27');
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

-- Dump completed on 2026-04-11 20:00:01
