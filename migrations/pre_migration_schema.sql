-- The database schema from before migrations were added: what
-- db.create_all() built from the models at commit 108606a. The first
-- migration (b96bd65a7506) starts from this, so the Migrations workflow loads
-- it and runs every migration on top. Don't edit it to match new models.
--
-- MySQL dump 10.13  Distrib 8.4.13, for Linux (aarch64)
--
-- Host: localhost    Database: fbc
-- ------------------------------------------------------
-- Server version	8.4.13

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `artist`
--

DROP TABLE IF EXISTS `artist`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `artist` (
  `artist_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `sort_name` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`artist_id`),
  UNIQUE KEY `artist_id` (`artist_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `artist_discogs_release`
--

DROP TABLE IF EXISTS `artist_discogs_release`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `artist_discogs_release` (
  `id` int NOT NULL AUTO_INCREMENT,
  `artist_id` int DEFAULT NULL,
  `release_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `artist_id` (`artist_id`),
  KEY `release_id` (`release_id`),
  CONSTRAINT `artist_discogs_release_ibfk_1` FOREIGN KEY (`artist_id`) REFERENCES `artist` (`artist_id`),
  CONSTRAINT `artist_discogs_release_ibfk_2` FOREIGN KEY (`release_id`) REFERENCES `discogs_release` (`release_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `discogs_instance`
--

DROP TABLE IF EXISTS `discogs_instance`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `discogs_instance` (
  `instance_id` int NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`instance_id`),
  UNIQUE KEY `instance_id` (`instance_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `discogs_instance_discogs_release`
--

DROP TABLE IF EXISTS `discogs_instance_discogs_release`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `discogs_instance_discogs_release` (
  `id` int NOT NULL AUTO_INCREMENT,
  `instance_id` int DEFAULT NULL,
  `release_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `instance_id` (`instance_id`),
  KEY `release_id` (`release_id`),
  CONSTRAINT `discogs_instance_discogs_release_ibfk_1` FOREIGN KEY (`instance_id`) REFERENCES `discogs_instance` (`instance_id`),
  CONSTRAINT `discogs_instance_discogs_release_ibfk_2` FOREIGN KEY (`release_id`) REFERENCES `discogs_release` (`release_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `discogs_release`
--

DROP TABLE IF EXISTS `discogs_release`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `discogs_release` (
  `release_id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(200) NOT NULL,
  `year` int DEFAULT NULL,
  `master_id` int DEFAULT NULL,
  `master_year` int DEFAULT NULL,
  `formats` text,
  `released` varchar(20) DEFAULT NULL,
  `notes` text,
  `folder_id` int DEFAULT NULL,
  `date_added` date DEFAULT NULL,
  PRIMARY KEY (`release_id`),
  UNIQUE KEY `release_id` (`release_id`),
  KEY `folder_id` (`folder_id`),
  KEY `ix_discogs_release_title` (`title`),
  CONSTRAINT `discogs_release_ibfk_1` FOREIGN KEY (`folder_id`) REFERENCES `folder` (`folder_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `ebay_listing`
--

DROP TABLE IF EXISTS `ebay_listing`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `ebay_listing` (
  `item_id` varchar(20) NOT NULL,
  `title` varchar(100) DEFAULT NULL,
  `price` float DEFAULT NULL,
  `url` varchar(255) DEFAULT NULL,
  `start_time` datetime DEFAULT NULL,
  PRIMARY KEY (`item_id`),
  UNIQUE KEY `item_id` (`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `ebay_order`
--

DROP TABLE IF EXISTS `ebay_order`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `ebay_order` (
  `order_id` varchar(20) NOT NULL,
  `created_time` datetime DEFAULT NULL,
  `buyer` varchar(50) DEFAULT NULL,
  `price` float DEFAULT NULL,
  `title` varchar(100) DEFAULT NULL,
  `ebay_listing_id` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`order_id`),
  UNIQUE KEY `order_id` (`order_id`),
  KEY `ebay_listing_id` (`ebay_listing_id`),
  CONSTRAINT `ebay_order_ibfk_1` FOREIGN KEY (`ebay_listing_id`) REFERENCES `ebay_listing` (`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `folder`
--

DROP TABLE IF EXISTS `folder`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `folder` (
  `folder_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  PRIMARY KEY (`folder_id`),
  UNIQUE KEY `folder_id` (`folder_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `genre`
--

DROP TABLE IF EXISTS `genre`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `genre` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `genre_discogs_release`
--

DROP TABLE IF EXISTS `genre_discogs_release`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `genre_discogs_release` (
  `id` int NOT NULL AUTO_INCREMENT,
  `genre_id` int DEFAULT NULL,
  `release_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `genre_id` (`genre_id`),
  KEY `release_id` (`release_id`),
  CONSTRAINT `genre_discogs_release_ibfk_1` FOREIGN KEY (`genre_id`) REFERENCES `genre` (`id`),
  CONSTRAINT `genre_discogs_release_ibfk_2` FOREIGN KEY (`release_id`) REFERENCES `discogs_release` (`release_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `purchase_lot`
--

DROP TABLE IF EXISTS `purchase_lot`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `purchase_lot` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_id` varchar(24) DEFAULT NULL,
  `name` varchar(100) NOT NULL,
  `date` date NOT NULL,
  `price` float NOT NULL,
  `notes` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `purchase_order`
--

DROP TABLE IF EXISTS `purchase_order`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `purchase_order` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_id` varchar(24) DEFAULT NULL,
  `date` date NOT NULL,
  `price` float NOT NULL,
  `notes` varchar(100) DEFAULT NULL,
  `link` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `purchase_order_supply`
--

DROP TABLE IF EXISTS `purchase_order_supply`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `purchase_order_supply` (
  `id` int NOT NULL AUTO_INCREMENT,
  `purchase_order_id` int DEFAULT NULL,
  `supply_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `purchase_order_id` (`purchase_order_id`),
  KEY `supply_id` (`supply_id`),
  CONSTRAINT `purchase_order_supply_ibfk_1` FOREIGN KEY (`purchase_order_id`) REFERENCES `purchase_order` (`id`),
  CONSTRAINT `purchase_order_supply_ibfk_2` FOREIGN KEY (`supply_id`) REFERENCES `supply` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `sales_receipt`
--

DROP TABLE IF EXISTS `sales_receipt`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `sales_receipt` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_id` varchar(24) DEFAULT NULL,
  `date` date NOT NULL,
  `ebay_order_id` varchar(20) DEFAULT NULL,
  `sold_price` float DEFAULT NULL,
  `net_sold` float DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ebay_order_id` (`ebay_order_id`),
  CONSTRAINT `sales_receipt_ibfk_1` FOREIGN KEY (`ebay_order_id`) REFERENCES `ebay_order` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `storage_box`
--

DROP TABLE IF EXISTS `storage_box`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `storage_box` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_id` varchar(24) DEFAULT NULL,
  `name` varchar(100) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `style`
--

DROP TABLE IF EXISTS `style`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `style` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `style_discogs_release`
--

DROP TABLE IF EXISTS `style_discogs_release`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `style_discogs_release` (
  `id` int NOT NULL AUTO_INCREMENT,
  `style_id` int DEFAULT NULL,
  `release_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `style_id` (`style_id`),
  KEY `release_id` (`release_id`),
  CONSTRAINT `style_discogs_release_ibfk_1` FOREIGN KEY (`style_id`) REFERENCES `style` (`id`),
  CONSTRAINT `style_discogs_release_ibfk_2` FOREIGN KEY (`release_id`) REFERENCES `discogs_release` (`release_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `supply`
--

DROP TABLE IF EXISTS `supply`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `supply` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_id` varchar(24) DEFAULT NULL,
  `name` varchar(100) NOT NULL,
  `quantity` int NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `unit`
--

DROP TABLE IF EXISTS `unit`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `unit` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_id` varchar(24) DEFAULT NULL,
  `name` varchar(200) NOT NULL,
  `unit_type` varchar(30) DEFAULT NULL,
  `description` varchar(300) DEFAULT NULL,
  `discogs_release_id` int DEFAULT NULL,
  `discogs_instance_id` int DEFAULT NULL,
  `ebay_listing_id` varchar(20) DEFAULT NULL,
  `purchase_lot_id` int DEFAULT NULL,
  `storage_box_id` int DEFAULT NULL,
  `grading` varchar(20) DEFAULT NULL,
  `pressing` varchar(100) DEFAULT NULL,
  `matrix` varchar(100) DEFAULT NULL,
  `notes` varchar(100) DEFAULT NULL,
  `retail_price` float DEFAULT NULL,
  `sold` tinyint(1) DEFAULT NULL,
  `sales_receipt_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `discogs_release_id` (`discogs_release_id`),
  KEY `discogs_instance_id` (`discogs_instance_id`),
  KEY `ebay_listing_id` (`ebay_listing_id`),
  KEY `purchase_lot_id` (`purchase_lot_id`),
  KEY `storage_box_id` (`storage_box_id`),
  KEY `sales_receipt_id` (`sales_receipt_id`),
  KEY `ix_unit_sold` (`sold`),
  KEY `ix_unit_name` (`name`),
  KEY `ix_unit_retail_price` (`retail_price`),
  CONSTRAINT `unit_ibfk_1` FOREIGN KEY (`discogs_release_id`) REFERENCES `discogs_release` (`release_id`),
  CONSTRAINT `unit_ibfk_2` FOREIGN KEY (`discogs_instance_id`) REFERENCES `discogs_instance` (`instance_id`),
  CONSTRAINT `unit_ibfk_3` FOREIGN KEY (`ebay_listing_id`) REFERENCES `ebay_listing` (`item_id`),
  CONSTRAINT `unit_ibfk_4` FOREIGN KEY (`purchase_lot_id`) REFERENCES `purchase_lot` (`id`),
  CONSTRAINT `unit_ibfk_5` FOREIGN KEY (`storage_box_id`) REFERENCES `storage_box` (`id`),
  CONSTRAINT `unit_ibfk_6` FOREIGN KEY (`sales_receipt_id`) REFERENCES `sales_receipt` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-10-30 20:53:45
