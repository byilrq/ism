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
(5,'DMDL162241','','高频',NULL,'YAE-B4','在库',NULL,'2026-04-09 05:06:03','2026-04-09 05:06:03'),
(6,'DMDL162245','','高频',NULL,'YAE-B4','在库',NULL,'2026-04-09 05:07:00','2026-04-09 05:07:00'),
(7,'DMDL162242','','高频',NULL,'YAE-B4','在库',NULL,'2026-04-09 05:07:58','2026-04-09 05:07:58'),
(8,'DMDL162497','','高频',NULL,'YAE-B5','在库',NULL,'2026-04-09 05:10:17','2026-04-09 05:10:17'),
(9,'DMDL162498','','高频',NULL,'YAE-B5','在库',NULL,'2026-04-09 05:11:14','2026-04-09 05:11:14'),
(10,'DMDL162500','','高频',NULL,'YAE-B5','在库',NULL,'2026-04-09 05:12:06','2026-04-09 05:12:06'),
(11,'DMDL162495','','高频',NULL,'YAE-B5','在库',NULL,'2026-04-09 05:13:17','2026-04-09 05:13:17'),
(12,'DMDL162496','','高频',NULL,'YAE-B5','在库',NULL,'2026-04-09 05:14:14','2026-04-09 05:14:14'),
(13,'DMDL162499','','高频',NULL,'YAE-B5','在库',NULL,'2026-04-09 05:14:45','2026-04-09 05:14:45'),
(14,'DMDL162524','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:19:35','2026-04-09 05:19:35'),
(15,'DMDL162525','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:20:14','2026-04-09 05:20:14'),
(16,'DMDL162526','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:20:57','2026-04-09 05:20:57'),
(17,'DMDL162521','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:21:32','2026-04-09 05:21:32'),
(18,'DMDL162523','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:22:01','2026-04-09 05:22:01'),
(19,'DMDL162522','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:24:01','2026-04-09 05:24:01'),
(20,'DMDL162635','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:29:00','2026-04-09 05:29:00'),
(21,'DMDL162530','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:29:36','2026-04-09 05:29:36'),
(22,'DMDL162890','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:29:59','2026-04-09 05:29:59'),
(23,'DMDL162608','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:30:22','2026-04-09 05:30:22'),
(24,'DMDL162639','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:30:48','2026-04-09 05:30:48'),
(25,'DMDL162638','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:31:07','2026-04-09 05:31:07'),
(26,'DMDL162894','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-09 05:31:44','2026-04-09 05:31:44'),
(27,'DMDL162897','','高频',NULL,'YAE-C3','在库',NULL,'2026-04-09 05:32:59','2026-04-09 05:32:59'),
(28,'DMDL160069','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-09 05:38:39','2026-04-09 05:38:39'),
(29,'DMDL161900','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:40:42','2026-04-09 05:40:42'),
(30,'DMDL161911','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:41:19','2026-04-09 05:41:19'),
(31,'DMDL161913','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:41:38','2026-04-09 05:41:38'),
(32,'DMDL161896','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:42:37','2026-04-09 05:42:37'),
(33,'DMDL161895','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:43:41','2026-04-09 05:43:41'),
(34,'DMDL161894','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:44:13','2026-04-09 05:44:13'),
(35,'DMDL161917','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:44:34','2026-04-09 05:44:34'),
(36,'DMDL161885','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:45:01','2026-04-09 05:45:01'),
(37,'DMDL161891','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:45:33','2026-04-09 05:45:33'),
(38,'DMDL161893','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:46:05','2026-04-09 05:46:05'),
(39,'DMDL161866','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:46:53','2026-04-09 05:46:53'),
(40,'DMDL161906','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:47:37','2026-04-09 05:47:37'),
(41,'DMDL161902','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:48:06','2026-04-09 05:48:06'),
(42,'DMDL161912','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:48:31','2026-04-09 05:48:31'),
(43,'DMDL161916','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:49:09','2026-04-09 05:49:09'),
(44,'DMDL161909','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:49:38','2026-04-09 05:49:38'),
(45,'DMDL161915','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:50:01','2026-04-09 05:50:01'),
(46,'DMDL161889','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:50:27','2026-04-09 05:50:27'),
(47,'DMDL161898','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:50:48','2026-04-09 05:50:48'),
(48,'DMDL161907','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:51:34','2026-04-09 05:51:34'),
(49,'DMDL161914','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:51:55','2026-04-09 05:51:55'),
(50,'DMDL161867','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:52:23','2026-04-09 05:52:23'),
(51,'DMDL161892','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:53:01','2026-04-09 05:53:01'),
(52,'DMDL161897','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:53:24','2026-04-09 05:53:24'),
(53,'DMDL161908','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:53:47','2026-04-09 05:53:47'),
(54,'DMDL161910','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:54:11','2026-04-09 05:54:11'),
(55,'DMDL161899','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:54:30','2026-04-09 05:54:30'),
(56,'DMDL161905','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:54:53','2026-04-09 05:54:53'),
(57,'DMDL161865','','高频',NULL,'YAF-C4','在库',NULL,'2026-04-09 05:55:18','2026-04-09 05:55:18'),
(58,'DMDL161943','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:18:13','2026-04-09 06:18:13'),
(59,'DMDL161931','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:18:44','2026-04-09 06:18:44'),
(60,'DMDL161947','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:19:12','2026-04-09 06:19:12'),
(61,'DMDL161939','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:19:32','2026-04-09 06:19:32'),
(62,'DMDL161936','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:19:56','2026-04-09 06:19:56'),
(63,'DMDL161945','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:21:08','2026-04-09 06:21:08'),
(64,'DMDL161934','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:21:33','2026-04-09 06:21:33'),
(65,'DMDL161941','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:22:00','2026-04-09 06:22:00'),
(66,'DMDL161957','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:22:32','2026-04-09 06:22:32'),
(67,'DMDL161961','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:23:28','2026-04-09 06:23:28'),
(68,'DMDL161932','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:23:50','2026-04-09 06:23:50'),
(69,'DMDL161951','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:24:17','2026-04-09 06:24:17'),
(70,'DMDL161946','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:24:44','2026-04-09 06:24:44'),
(71,'DMDL161948','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:25:13','2026-04-09 06:25:13'),
(72,'DMDL161940','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:25:55','2026-04-09 06:25:55'),
(73,'DMDL161937','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:26:13','2026-04-09 06:26:13'),
(74,'DMDL161938','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:26:40','2026-04-09 06:26:40'),
(75,'DMDL161956','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:27:02','2026-04-09 06:27:02'),
(76,'DMDL161960','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:27:22','2026-04-09 06:27:22'),
(77,'DMDL161955','','高频',NULL,'YAE-B2','在库',NULL,'2026-04-09 06:27:59','2026-04-09 06:27:59'),
(78,'DMDL161668','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:37:38','2026-04-09 06:37:38'),
(79,'DMDL161680','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:37:58','2026-04-09 06:37:58'),
(80,'DMDL161667','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:38:23','2026-04-09 06:38:23'),
(81,'DMDL161672','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:38:50','2026-04-09 06:38:50'),
(82,'DMDL161670','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:39:17','2026-04-09 06:39:17'),
(83,'DMDL161821','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:39:43','2026-04-09 06:39:43'),
(84,'DMDL161666','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:40:22','2026-04-09 06:40:22'),
(85,'DMDL161669','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:40:46','2026-04-09 06:40:46'),
(86,'DMDL161674','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:41:09','2026-04-09 06:41:09'),
(87,'DMDL161673','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:41:36','2026-04-09 06:41:36'),
(88,'DMDL161726','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:43:01','2026-04-09 06:43:01'),
(89,'DMDL162115','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:43:31','2026-04-09 06:43:31'),
(90,'DMDL161857','','高频',NULL,'YAE-A3','在库',NULL,'2026-04-09 06:44:52','2026-04-09 06:44:52'),
(91,'DMDL161836','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:47:26','2026-04-09 06:47:26'),
(92,'DMDL161824','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:47:55','2026-04-09 06:47:55'),
(93,'DMDL162218','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:50:16','2026-04-09 06:50:16'),
(94,'DMDL162215','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:50:40','2026-04-09 06:50:40'),
(95,'DMDL162226','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:51:20','2026-04-09 06:51:20'),
(96,'DMDL162214','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:51:49','2026-04-09 06:51:49'),
(97,'DMDL162213','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:52:12','2026-04-09 06:52:12'),
(98,'DMDL162202','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:52:42','2026-04-09 06:52:42'),
(99,'DMDL162203','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:53:09','2026-04-09 06:53:09'),
(100,'DMDL162206','','高频',NULL,'YAF-B5','在库',NULL,'2026-04-09 06:53:44','2026-04-09 06:53:44'),
(101,'DMDL161767','','高频',NULL,'YAF-B4','在库',NULL,'2026-04-09 06:56:53','2026-04-09 06:58:49'),
(102,'DMDL161764','','高频',NULL,'YAF-B4','在库',NULL,'2026-04-09 06:58:06','2026-04-09 06:58:06'),
(103,'DMDL161770','','高频',NULL,'YAF-B4','在库',NULL,'2026-04-09 06:59:31','2026-04-09 06:59:31'),
(104,'DMDL161772','','高频',NULL,'YAF-B4','在库',NULL,'2026-04-09 06:59:51','2026-04-09 06:59:51'),
(105,'DMDL163022','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:04:22','2026-04-09 07:04:22'),
(106,'DMDL161747','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-09 07:06:24','2026-04-10 05:06:03'),
(107,'DMDL163003','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:06:45','2026-04-09 07:06:45'),
(108,'DMDL162970','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:07:06','2026-04-09 07:07:06'),
(109,'DMDL162993','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:07:35','2026-04-09 07:07:35'),
(110,'DMDL162994','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:08:11','2026-04-09 07:08:11'),
(111,'DMDL162996','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:08:36','2026-04-09 07:08:36'),
(112,'DMDL162997','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:09:28','2026-04-09 07:09:28'),
(113,'DMDL162987','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:09:49','2026-04-09 07:09:49'),
(114,'DMDL162991','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:10:15','2026-04-09 07:10:15'),
(115,'DMDL162961','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:10:34','2026-04-09 07:10:34'),
(116,'DMDL162681','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:20:00','2026-04-09 07:20:00'),
(117,'DMDL161950','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:20:34','2026-04-09 07:20:34'),
(118,'DMDL160101','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:21:18','2026-04-09 07:21:18'),
(119,'DMDL160097','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:21:45','2026-04-09 07:21:45'),
(120,'DMDL160079','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:22:09','2026-04-09 07:22:09'),
(121,'DMDL160085','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:22:32','2026-04-09 07:22:32'),
(122,'DMDL160083','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:23:04','2026-04-09 07:23:04'),
(123,'DMDL160084','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:23:25','2026-04-09 07:23:25'),
(124,'DMDL160087','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:23:51','2026-04-09 07:23:51'),
(125,'DMDL160086','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:24:12','2026-04-09 07:24:12'),
(126,'DMDL160089','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:25:07','2026-04-09 07:25:07'),
(127,'DMDL160088','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-09 07:25:18','2026-04-09 07:25:18'),
(128,'DMDL160076','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-09 07:26:08','2026-04-09 07:26:08'),
(129,'DMDL162966','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:47:30','2026-04-09 07:47:30'),
(130,'DMDL162969','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:47:49','2026-04-09 07:47:49'),
(131,'DMDL162965','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:48:12','2026-04-09 07:48:12'),
(132,'DMDL162963','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:48:30','2026-04-09 07:48:30'),
(133,'DMDL163001','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:48:52','2026-04-09 07:48:52'),
(134,'DMDL162968','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:49:08','2026-04-09 07:49:08'),
(135,'DMDL162962','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:49:24','2026-04-09 07:49:24'),
(136,'DMDL162597','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:49:47','2026-04-09 07:49:47'),
(137,'DMDL180032','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:50:08','2026-04-09 07:50:08'),
(138,'DMDL161903','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:50:28','2026-04-09 07:50:28'),
(139,'DMDL163004','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:50:51','2026-04-09 07:50:51'),
(140,'DMDL180034','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:51:12','2026-04-09 07:51:12'),
(141,'DMDL161901','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:51:29','2026-04-09 07:51:29'),
(142,'DMDL190649','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:51:55','2026-04-09 07:51:55'),
(143,'DMDL161904','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:52:27','2026-04-09 07:52:27'),
(144,'DMDL163002','','高频',NULL,'DLA-A1','在库',NULL,'2026-04-09 07:52:44','2026-04-09 07:52:44'),
(145,'DMDL190487','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 07:56:34','2026-04-09 07:56:34'),
(146,'DMDL190476','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 07:57:32','2026-04-09 07:57:32'),
(147,'DMDL190469','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 07:58:15','2026-04-09 07:58:15'),
(148,'DMDL181033','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 07:58:59','2026-04-09 07:59:16'),
(149,'DMDL181032','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 07:59:53','2026-04-09 07:59:53'),
(150,'DMDL181034','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:00:09','2026-04-09 08:00:09'),
(151,'DMDL181029','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:00:29','2026-04-09 08:00:29'),
(152,'DMDL181031','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:00:51','2026-04-09 08:00:51'),
(153,'DMDL181028','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:01:20','2026-04-09 08:01:20'),
(154,'DMDL181027','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:02:57','2026-04-09 08:02:57'),
(155,'DMDL181024','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:03:21','2026-04-09 08:03:21'),
(156,'DMDL181026','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:03:43','2026-04-09 08:03:43'),
(157,'DMDL181025','','高频',NULL,'DLA-A2','在库',NULL,'2026-04-09 08:04:01','2026-04-09 08:04:01'),
(159,'DMDL160197','','高频',NULL,'DLA-A3','在库',NULL,'2026-04-10 04:55:48','2026-04-10 04:55:48'),
(160,'DMDL160205','','高频',NULL,'DLA-A3','在库',NULL,'2026-04-10 04:56:33','2026-04-10 04:56:33'),
(161,'DMDL162189','','高频',NULL,'DLA-A3','在库',NULL,'2026-04-10 04:57:00','2026-04-10 04:57:00'),
(162,'DMDL200040','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:07:26','2026-04-10 05:07:26'),
(163,'DMDL200050','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:07:46','2026-04-10 05:07:46'),
(164,'DMDL200055','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:08:07','2026-04-10 05:08:07'),
(165,'DMDL200061','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:08:25','2026-04-10 05:08:25'),
(166,'DMDL200059','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:08:50','2026-04-10 05:08:50'),
(167,'DMDL200056','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:09:50','2026-04-10 05:09:50'),
(168,'DMDL200058','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:10:25','2026-04-10 05:10:25'),
(169,'DMDL200060','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:10:50','2026-04-10 05:10:50'),
(170,'DMDL200054','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:11:17','2026-04-10 05:11:17'),
(171,'DMDL200057','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:11:46','2026-04-10 05:11:46'),
(172,'DMDL200053','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:12:13','2026-04-10 05:12:13'),
(173,'DMDL200062','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:12:36','2026-04-10 05:12:36'),
(174,'DMDL200048','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:13:06','2026-04-10 05:13:06'),
(175,'DMDL200039','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:13:38','2026-04-10 05:13:38'),
(176,'DMDL200038','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:13:59','2026-04-10 05:13:59'),
(177,'DMDL200041','','高频',NULL,'DLA-A4','在库',NULL,'2026-04-10 05:14:23','2026-04-10 05:14:23'),
(178,'DMDL180142','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:28:53','2026-04-10 05:29:57'),
(179,'DMDL180141','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:29:37','2026-04-10 05:29:37'),
(180,'DMDL180152','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:30:57','2026-04-10 05:30:57'),
(181,'DMDL180170','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:31:27','2026-04-10 05:31:27'),
(182,'DMDL180153','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:32:16','2026-04-10 05:32:16'),
(183,'DMDL180151','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:32:44','2026-04-10 05:32:44'),
(184,'DMDL180154','','高频',NULL,'YAF-C5','在库',NULL,'2026-04-10 05:33:11','2026-04-10 05:33:11'),
(185,'DMDL210038','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-10 05:42:21','2026-04-10 05:42:21'),
(186,'DMDL210032','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:43:41','2026-04-10 05:43:41'),
(187,'DMDL210052','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:44:32','2026-04-10 05:44:32'),
(188,'DMDL210035','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:45:07','2026-04-10 05:45:07'),
(189,'DMDL180393','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:50:04','2026-04-10 05:50:04'),
(190,'DMDL180392','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:50:24','2026-04-10 05:50:24'),
(191,'DMDL180394','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:50:48','2026-04-10 05:50:48'),
(192,'DMDL210050','','高频',NULL,'YAF-B1','在库',NULL,'2026-04-10 05:52:55','2026-04-10 05:53:21'),
(193,'DMDL210455','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-10 05:54:20','2026-04-10 05:54:20'),
(194,'DMDL162532','','高频',NULL,'YAF-C1','在库',NULL,'2026-04-10 06:01:45','2026-04-10 06:01:45'),
(195,'DMDL180033','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:03:08','2026-04-10 06:03:08'),
(196,'DMDL162992','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:05:32','2026-04-10 06:05:32'),
(197,'DMDL210426','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:06:03','2026-04-10 06:06:03'),
(198,'DMDL161748','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:06:44','2026-04-10 06:06:44'),
(199,'DMDL210428','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:07:52','2026-04-10 06:07:52'),
(200,'DMDL200130','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:08:17','2026-04-10 06:08:17'),
(201,'DMDL200113','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:08:43','2026-04-10 06:08:43'),
(202,'DMDL200134','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:09:02','2026-04-10 06:09:02'),
(203,'DMDL200131','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:09:20','2026-04-10 06:09:20'),
(204,'DMDL200135','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:12:00','2026-04-10 06:12:00'),
(205,'DMDL200112','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:12:19','2026-04-10 06:12:19'),
(206,'DMDL200107','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:12:40','2026-04-10 06:12:40'),
(207,'DMDL200111','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:12:58','2026-04-10 06:12:58'),
(208,'DMDL200100','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:13:21','2026-04-10 06:13:21'),
(209,'DMDL200114','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:13:36','2026-04-10 06:13:36'),
(210,'DMDL200101','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:13:51','2026-04-10 06:13:51'),
(211,'DMDL200104','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:14:08','2026-04-10 06:14:08'),
(212,'DMDL200106','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:14:20','2026-04-10 06:14:20'),
(213,'DMDL200105','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:14:42','2026-04-10 06:14:42'),
(214,'DMDL200103','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:14:55','2026-04-10 06:14:55'),
(215,'DMDL200110','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:15:22','2026-04-10 06:15:22'),
(216,'DMDL200108','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:15:38','2026-04-10 06:15:38'),
(217,'DMDL200102','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:15:50','2026-04-10 06:15:50'),
(218,'DMDL200132','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:19:06','2026-04-10 06:19:06'),
(219,'DMDL210427','','高频',NULL,'YAF-A1','在库',NULL,'2026-04-10 06:32:58','2026-04-10 06:32:58'),
(220,'DMDL170241','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:47:37','2026-04-10 06:47:37'),
(221,'DMDL160613','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-10 06:49:24','2026-04-10 06:49:24'),
(222,'DMDL160617','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-10 06:49:55','2026-04-10 06:49:55'),
(223,'DMDL160619','','高频',NULL,'YAF-C2','在库',NULL,'2026-04-10 06:50:09','2026-04-10 06:50:09'),
(224,'DMDL170242','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:50:49','2026-04-10 06:50:49'),
(225,'DMDL170229','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:51:17','2026-04-10 06:51:17'),
(226,'DMDL170244','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:51:32','2026-04-10 06:51:32'),
(227,'DMDL170245','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:51:52','2026-04-10 06:51:52'),
(228,'DMDL170243','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:52:13','2026-04-10 06:52:13'),
(229,'DMDL170237','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:52:32','2026-04-10 06:52:32'),
(230,'DMDL170238','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:52:46','2026-04-10 06:52:46'),
(231,'DMDL170230','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:53:08','2026-04-10 06:53:08'),
(232,'DMDL170236','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:53:23','2026-04-10 06:53:23'),
(233,'DMDL170226','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:53:56','2026-04-10 06:53:56'),
(234,'DMDL170239','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:54:13','2026-04-10 06:54:13'),
(235,'DMDL170227','','高频',NULL,'YAF-B2','在库',NULL,'2026-04-10 06:54:33','2026-04-10 06:54:33'),
(236,'DMDL162527','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 06:58:28','2026-04-10 06:58:28'),
(237,'DMDL162636','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 07:00:20','2026-04-10 07:00:20'),
(238,'DMDL162528','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 07:00:33','2026-04-10 07:00:33'),
(239,'DMDL162626','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 07:00:59','2026-04-10 07:00:59'),
(240,'DMDL160102','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 07:01:29','2026-04-10 07:01:29'),
(241,'DMDL170547','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 07:02:57','2026-04-10 07:02:57'),
(242,'DMDL180040','','高频',NULL,'YAE-C1','在库',NULL,'2026-04-10 07:03:24','2026-04-10 07:03:24'),
(243,'DMDL190494','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:16:22','2026-04-10 07:16:22'),
(244,'DMDL190503','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:17:01','2026-04-10 07:17:01'),
(245,'DMDL190504','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:17:46','2026-04-10 07:17:46'),
(246,'DMDL190486','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:18:17','2026-04-10 07:18:17'),
(247,'DMDL190485','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:18:41','2026-04-10 07:18:41'),
(248,'DMDL190502','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:19:03','2026-04-10 07:19:03'),
(249,'DMDL190489','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:19:25','2026-04-10 07:19:25'),
(250,'DMDL190505','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:19:51','2026-04-10 07:19:51'),
(251,'DMDL190493','','高频',NULL,'YAE-B1','在库',NULL,'2026-04-10 07:20:23','2026-04-10 07:20:23'),
(252,'DMDL210439','','高频',NULL,'YAF-B4','在库',NULL,'2026-04-10 07:23:49','2026-04-10 07:23:49'),
(253,'DMDL210022','','高频',NULL,'YAF-B4','在库','不合格','2026-04-10 07:24:48','2026-04-10 07:25:44'),
(254,'DMDL210023','','高频',NULL,'YAF-B4','在库','不合格','2026-04-10 07:25:09','2026-04-10 07:25:15');
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

-- Dump completed on 2026-04-11 20:00:01
