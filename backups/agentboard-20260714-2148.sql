-- AgentBoard Database Dump
-- Generated: 2026-07-14 21:48
-- Projects: 4, Epics: 14, Stories: 44, Tasks: 117

SET NAMES utf8mb4;

PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE agent_runs (
	id INTEGER NOT NULL, 
	schedule_id INTEGER NOT NULL, 
	task_id INTEGER, 
	status VARCHAR(20) DEFAULT 'pending' NOT NULL, 
	idempotency_key VARCHAR(128), 
	started_at DATETIME, 
	finished_at DATETIME, 
	output TEXT, 
	error_message TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(schedule_id) REFERENCES agent_schedules (id) ON DELETE CASCADE, 
	FOREIGN KEY(task_id) REFERENCES tasks (id), 
	UNIQUE (idempotency_key)
);
CREATE TABLE agent_schedules (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	schedule_type VARCHAR(10) DEFAULT 'cron' NOT NULL, 
	cron_expr VARCHAR(100), 
	enabled BOOLEAN DEFAULT 1 NOT NULL, 
	next_run_at DATETIME, 
	last_run_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);
CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL, 
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
INSERT INTO """alembic_version""" VALUES('9f8c2e7d1a4c');
CREATE TABLE api_keys (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	key_prefix VARCHAR(20) NOT NULL, 
	key_hash VARCHAR(64) NOT NULL, 
	permissions TEXT DEFAULT '[]' NOT NULL, 
	enabled BOOLEAN DEFAULT 1 NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	last_used_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (key_hash)
);
CREATE TABLE attachments (
	id INTEGER NOT NULL, 
	task_id INTEGER NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	original_name VARCHAR(500) NOT NULL, 
	size INTEGER NOT NULL, 
	mime_type VARCHAR(200) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);
CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(50) NOT NULL, 
	entity_type VARCHAR(30) NOT NULL, 
	entity_id INTEGER, 
	method VARCHAR(10) NOT NULL, 
	path VARCHAR(500) NOT NULL, 
	ip_address VARCHAR(45), 
	user_agent VARCHAR(500), 
	request_body TEXT, 
	response_status INTEGER, 
	duration_ms INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);
INSERT INTO """audit_logs""" VALUES(1,NULL,'GET','project',3,'GET','/api/projects/3/stats','172.17.0.1','curl/8.19.0',NULL,200,70,'2026-07-14 13:35:33.408582');
INSERT INTO """audit_logs""" VALUES(2,NULL,'GET','project',3,'GET','/api/projects/3/sprints','172.17.0.1','curl/8.19.0',NULL,200,80,'2026-07-14 13:35:42.427589');
INSERT INTO """audit_logs""" VALUES(3,NULL,'GET','unknown',NULL,'GET','/api/notifications','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,401,8,'2026-07-14 13:36:07.875608');
INSERT INTO """audit_logs""" VALUES(4,NULL,'GET','unknown',NULL,'GET','/api/notifications/unread-count','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,401,49,'2026-07-14 13:36:07.919818');
INSERT INTO """audit_logs""" VALUES(5,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/auth/login','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,0,'2026-07-14 13:47:05.488286');
INSERT INTO """audit_logs""" VALUES(6,NULL,'POST','unknown',NULL,'POST','/api/auth/login','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0','{"""username""":"""jzhong2026""","""password""":"""***"""}',401,8,'2026-07-14 13:47:05.535607');
INSERT INTO """audit_logs""" VALUES(7,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/auth/register','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,0,'2026-07-14 13:47:11.645155');
INSERT INTO """audit_logs""" VALUES(8,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/projects','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,0,'2026-07-14 13:47:17.314351');
INSERT INTO """audit_logs""" VALUES(9,3,'GET','unknown',NULL,'GET','/api/projects','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,23,'2026-07-14 13:47:17.509166');
INSERT INTO """audit_logs""" VALUES(10,NULL,'OPTIONS','project',4,'OPTIONS','/api/projects/4/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,0,'2026-07-14 13:47:17.544178');
INSERT INTO """audit_logs""" VALUES(11,NULL,'OPTIONS','project',3,'OPTIONS','/api/projects/3/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,3,'2026-07-14 13:47:17.604850');
INSERT INTO """audit_logs""" VALUES(12,NULL,'OPTIONS','project',2,'OPTIONS','/api/projects/2/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,33,'2026-07-14 13:47:17.636961');
INSERT INTO """audit_logs""" VALUES(13,NULL,'OPTIONS','project',1,'OPTIONS','/api/projects/1/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,65,'2026-07-14 13:47:17.669371');
INSERT INTO """audit_logs""" VALUES(14,3,'GET','project',4,'GET','/api/projects/4/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,28,'2026-07-14 13:47:17.766205');
INSERT INTO """audit_logs""" VALUES(15,3,'GET','project',3,'GET','/api/projects/3/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,131,'2026-07-14 13:47:17.859305');
INSERT INTO """audit_logs""" VALUES(16,3,'GET','project',2,'GET','/api/projects/2/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,178,'2026-07-14 13:47:17.909387');
INSERT INTO """audit_logs""" VALUES(17,3,'GET','project',1,'GET','/api/projects/1/epics','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,223,'2026-07-14 13:47:17.957082');
INSERT INTO """audit_logs""" VALUES(18,NULL,'OPTIONS','epic',1,'OPTIONS','/api/epics/1/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,0,'2026-07-14 13:47:18.018975');
INSERT INTO """audit_logs""" VALUES(19,NULL,'OPTIONS','epic',2,'OPTIONS','/api/epics/2/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,5,'2026-07-14 13:47:18.057817');
INSERT INTO """audit_logs""" VALUES(20,NULL,'OPTIONS','epic',3,'OPTIONS','/api/epics/3/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,54,'2026-07-14 13:47:18.106950');
INSERT INTO """audit_logs""" VALUES(21,NULL,'OPTIONS','epic',4,'OPTIONS','/api/epics/4/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,95,'2026-07-14 13:47:18.148626');
INSERT INTO """audit_logs""" VALUES(22,NULL,'OPTIONS','epic',5,'OPTIONS','/api/epics/5/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,146,'2026-07-14 13:47:18.199228');
INSERT INTO """audit_logs""" VALUES(23,NULL,'OPTIONS','epic',6,'OPTIONS','/api/epics/6/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,187,'2026-07-14 13:47:18.240863');
INSERT INTO """audit_logs""" VALUES(24,NULL,'OPTIONS','epic',7,'OPTIONS','/api/epics/7/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,216,'2026-07-14 13:47:18.273213');
INSERT INTO """audit_logs""" VALUES(25,NULL,'OPTIONS','epic',8,'OPTIONS','/api/epics/8/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,4,'2026-07-14 13:47:18.308942');
INSERT INTO """audit_logs""" VALUES(26,NULL,'OPTIONS','epic',9,'OPTIONS','/api/epics/9/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,43,'2026-07-14 13:47:18.349706');
INSERT INTO """audit_logs""" VALUES(27,NULL,'OPTIONS','epic',10,'OPTIONS','/api/epics/10/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,72,'2026-07-14 13:47:18.377573');
INSERT INTO """audit_logs""" VALUES(28,NULL,'OPTIONS','epic',11,'OPTIONS','/api/epics/11/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,101,'2026-07-14 13:47:18.406842');
INSERT INTO """audit_logs""" VALUES(29,NULL,'OPTIONS','epic',12,'OPTIONS','/api/epics/12/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,133,'2026-07-14 13:47:18.439116');
INSERT INTO """audit_logs""" VALUES(30,NULL,'OPTIONS','epic',13,'OPTIONS','/api/epics/13/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,174,'2026-07-14 13:47:18.482520');
INSERT INTO """audit_logs""" VALUES(31,NULL,'OPTIONS','epic',14,'OPTIONS','/api/epics/14/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,8,'2026-07-14 13:47:18.525655');
INSERT INTO """audit_logs""" VALUES(32,3,'GET','epic',1,'GET','/api/epics/1/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,68,'2026-07-14 13:47:18.590923');
INSERT INTO """audit_logs""" VALUES(33,3,'GET','epic',6,'GET','/api/epics/6/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,128,'2026-07-14 13:47:18.649860');
INSERT INTO """audit_logs""" VALUES(34,3,'GET','epic',3,'GET','/api/epics/3/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,174,'2026-07-14 13:47:18.692282');
INSERT INTO """audit_logs""" VALUES(35,3,'GET','epic',2,'GET','/api/epics/2/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,212,'2026-07-14 13:47:18.730876');
INSERT INTO """audit_logs""" VALUES(36,3,'GET','epic',5,'GET','/api/epics/5/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,247,'2026-07-14 13:47:18.769656');
INSERT INTO """audit_logs""" VALUES(37,3,'GET','epic',4,'GET','/api/epics/4/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,222,'2026-07-14 13:47:18.800053');
INSERT INTO """audit_logs""" VALUES(38,3,'GET','epic',7,'GET','/api/epics/7/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,29,'2026-07-14 13:47:18.868422');
INSERT INTO """audit_logs""" VALUES(39,3,'GET','epic',12,'GET','/api/epics/12/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,62,'2026-07-14 13:47:18.907727');
INSERT INTO """audit_logs""" VALUES(40,3,'GET','epic',9,'GET','/api/epics/9/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,98,'2026-07-14 13:47:18.943634');
INSERT INTO """audit_logs""" VALUES(41,3,'GET','epic',8,'GET','/api/epics/8/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,128,'2026-07-14 13:47:18.973409');
INSERT INTO """audit_logs""" VALUES(42,3,'GET','epic',11,'GET','/api/epics/11/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,157,'2026-07-14 13:47:19.001311');
INSERT INTO """audit_logs""" VALUES(43,3,'GET','epic',10,'GET','/api/epics/10/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,183,'2026-07-14 13:47:19.028581');
INSERT INTO """audit_logs""" VALUES(44,3,'GET','epic',13,'GET','/api/epics/13/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,10,'2026-07-14 13:47:19.070401');
INSERT INTO """audit_logs""" VALUES(45,3,'GET','epic',14,'GET','/api/epics/14/stories','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,36,'2026-07-14 13:47:19.096385');
INSERT INTO """audit_logs""" VALUES(46,NULL,'OPTIONS','story',1,'OPTIONS','/api/stories/1/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,8,'2026-07-14 13:47:19.145092');
INSERT INTO """audit_logs""" VALUES(47,NULL,'OPTIONS','story',2,'OPTIONS','/api/stories/2/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,35,'2026-07-14 13:47:19.172545');
INSERT INTO """audit_logs""" VALUES(48,NULL,'OPTIONS','story',3,'OPTIONS','/api/stories/3/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,59,'2026-07-14 13:47:19.198152');
INSERT INTO """audit_logs""" VALUES(49,NULL,'OPTIONS','story',4,'OPTIONS','/api/stories/4/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,86,'2026-07-14 13:47:19.226826');
INSERT INTO """audit_logs""" VALUES(50,NULL,'OPTIONS','story',5,'OPTIONS','/api/stories/5/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,113,'2026-07-14 13:47:19.252688');
INSERT INTO """audit_logs""" VALUES(51,NULL,'OPTIONS','story',6,'OPTIONS','/api/stories/6/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,152,'2026-07-14 13:47:19.292876');
INSERT INTO """audit_logs""" VALUES(52,NULL,'OPTIONS','story',7,'OPTIONS','/api/stories/7/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,4,'2026-07-14 13:47:19.331584');
INSERT INTO """audit_logs""" VALUES(53,NULL,'OPTIONS','story',8,'OPTIONS','/api/stories/8/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,37,'2026-07-14 13:47:19.367772');
INSERT INTO """audit_logs""" VALUES(54,NULL,'OPTIONS','story',9,'OPTIONS','/api/stories/9/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,68,'2026-07-14 13:47:19.399229');
INSERT INTO """audit_logs""" VALUES(55,NULL,'OPTIONS','story',10,'OPTIONS','/api/stories/10/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,65,'2026-07-14 13:47:19.430716');
INSERT INTO """audit_logs""" VALUES(56,NULL,'OPTIONS','story',11,'OPTIONS','/api/stories/11/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,96,'2026-07-14 13:47:19.461339');
INSERT INTO """audit_logs""" VALUES(57,NULL,'OPTIONS','story',12,'OPTIONS','/api/stories/12/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,126,'2026-07-14 13:47:19.491263');
INSERT INTO """audit_logs""" VALUES(58,NULL,'OPTIONS','story',13,'OPTIONS','/api/stories/13/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,137,'2026-07-14 13:47:19.536514');
INSERT INTO """audit_logs""" VALUES(59,NULL,'OPTIONS','story',14,'OPTIONS','/api/stories/14/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,49,'2026-07-14 13:47:19.576243');
INSERT INTO """audit_logs""" VALUES(60,NULL,'OPTIONS','story',15,'OPTIONS','/api/stories/15/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,79,'2026-07-14 13:47:19.613596');
INSERT INTO """audit_logs""" VALUES(61,NULL,'OPTIONS','story',16,'OPTIONS','/api/stories/16/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,111,'2026-07-14 13:47:19.645400');
INSERT INTO """audit_logs""" VALUES(62,NULL,'OPTIONS','story',17,'OPTIONS','/api/stories/17/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,139,'2026-07-14 13:47:19.674301');
INSERT INTO """audit_logs""" VALUES(63,NULL,'OPTIONS','story',18,'OPTIONS','/api/stories/18/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,168,'2026-07-14 13:47:19.702725');
INSERT INTO """audit_logs""" VALUES(64,NULL,'OPTIONS','story',19,'OPTIONS','/api/stories/19/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,122,'2026-07-14 13:47:19.735114');
INSERT INTO """audit_logs""" VALUES(65,NULL,'OPTIONS','story',20,'OPTIONS','/api/stories/20/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,34,'2026-07-14 13:47:19.767460');
INSERT INTO """audit_logs""" VALUES(66,NULL,'OPTIONS','story',21,'OPTIONS','/api/stories/21/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,33,'2026-07-14 13:47:19.799038');
INSERT INTO """audit_logs""" VALUES(67,NULL,'OPTIONS','story',22,'OPTIONS','/api/stories/22/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,67,'2026-07-14 13:47:19.832840');
INSERT INTO """audit_logs""" VALUES(68,NULL,'OPTIONS','story',23,'OPTIONS','/api/stories/23/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,98,'2026-07-14 13:47:19.864409');
INSERT INTO """audit_logs""" VALUES(69,NULL,'OPTIONS','story',24,'OPTIONS','/api/stories/24/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,131,'2026-07-14 13:47:19.897391');
INSERT INTO """audit_logs""" VALUES(70,NULL,'OPTIONS','story',25,'OPTIONS','/api/stories/25/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,129,'2026-07-14 13:47:19.926154');
INSERT INTO """audit_logs""" VALUES(71,NULL,'OPTIONS','story',26,'OPTIONS','/api/stories/26/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,29,'2026-07-14 13:47:19.953952');
INSERT INTO """audit_logs""" VALUES(72,NULL,'OPTIONS','story',27,'OPTIONS','/api/stories/27/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,30,'2026-07-14 13:47:19.982425');
INSERT INTO """audit_logs""" VALUES(73,NULL,'OPTIONS','story',28,'OPTIONS','/api/stories/28/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,58,'2026-07-14 13:47:20.009744');
INSERT INTO """audit_logs""" VALUES(74,NULL,'OPTIONS','story',29,'OPTIONS','/api/stories/29/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,80,'2026-07-14 13:47:20.032459');
INSERT INTO """audit_logs""" VALUES(75,NULL,'OPTIONS','story',30,'OPTIONS','/api/stories/30/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,106,'2026-07-14 13:47:20.058379');
INSERT INTO """audit_logs""" VALUES(76,NULL,'OPTIONS','story',31,'OPTIONS','/api/stories/31/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,135,'2026-07-14 13:47:20.089514');
INSERT INTO """audit_logs""" VALUES(77,NULL,'OPTIONS','story',32,'OPTIONS','/api/stories/32/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,37,'2026-07-14 13:47:20.124581');
INSERT INTO """audit_logs""" VALUES(78,NULL,'OPTIONS','story',33,'OPTIONS','/api/stories/33/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,31,'2026-07-14 13:47:20.153109');
INSERT INTO """audit_logs""" VALUES(79,NULL,'OPTIONS','story',34,'OPTIONS','/api/stories/34/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,62,'2026-07-14 13:47:20.184167');
INSERT INTO """audit_logs""" VALUES(80,NULL,'OPTIONS','story',35,'OPTIONS','/api/stories/35/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,93,'2026-07-14 13:47:20.215889');
INSERT INTO """audit_logs""" VALUES(81,NULL,'OPTIONS','story',36,'OPTIONS','/api/stories/36/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,128,'2026-07-14 13:47:20.250326');
INSERT INTO """audit_logs""" VALUES(82,NULL,'OPTIONS','story',37,'OPTIONS','/api/stories/37/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,158,'2026-07-14 13:47:20.282973');
INSERT INTO """audit_logs""" VALUES(83,NULL,'OPTIONS','story',38,'OPTIONS','/api/stories/38/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,38,'2026-07-14 13:47:20.319942');
INSERT INTO """audit_logs""" VALUES(84,NULL,'OPTIONS','story',39,'OPTIONS','/api/stories/39/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,35,'2026-07-14 13:47:20.350802');
INSERT INTO """audit_logs""" VALUES(85,NULL,'OPTIONS','story',40,'OPTIONS','/api/stories/40/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,67,'2026-07-14 13:47:20.385163');
INSERT INTO """audit_logs""" VALUES(86,NULL,'OPTIONS','story',41,'OPTIONS','/api/stories/41/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,108,'2026-07-14 13:47:20.424178');
INSERT INTO """audit_logs""" VALUES(87,NULL,'OPTIONS','story',42,'OPTIONS','/api/stories/42/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,139,'2026-07-14 13:47:20.455309');
INSERT INTO """audit_logs""" VALUES(88,NULL,'OPTIONS','story',43,'OPTIONS','/api/stories/43/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,169,'2026-07-14 13:47:20.488778');
INSERT INTO """audit_logs""" VALUES(89,NULL,'OPTIONS','story',44,'OPTIONS','/api/stories/44/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,49,'2026-07-14 13:47:20.536826');
INSERT INTO """audit_logs""" VALUES(90,3,'GET','story',5,'GET','/api/stories/5/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,61,'2026-07-14 13:47:20.606255');
INSERT INTO """audit_logs""" VALUES(91,3,'GET','story',1,'GET','/api/stories/1/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,113,'2026-07-14 13:47:20.643595');
INSERT INTO """audit_logs""" VALUES(92,3,'GET','story',3,'GET','/api/stories/3/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,138,'2026-07-14 13:47:20.669076');
INSERT INTO """audit_logs""" VALUES(93,3,'GET','story',6,'GET','/api/stories/6/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,168,'2026-07-14 13:47:20.699594');
INSERT INTO """audit_logs""" VALUES(94,3,'GET','story',2,'GET','/api/stories/2/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,200,'2026-07-14 13:47:20.730351');
INSERT INTO """audit_logs""" VALUES(95,3,'GET','story',4,'GET','/api/stories/4/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,185,'2026-07-14 13:47:20.762111');
INSERT INTO """audit_logs""" VALUES(96,3,'GET','story',7,'GET','/api/stories/7/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,34,'2026-07-14 13:47:20.846950');
INSERT INTO """audit_logs""" VALUES(97,3,'GET','story',10,'GET','/api/stories/10/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,92,'2026-07-14 13:47:20.901442');
INSERT INTO """audit_logs""" VALUES(98,3,'GET','story',8,'GET','/api/stories/8/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,139,'2026-07-14 13:47:20.945910');
INSERT INTO """audit_logs""" VALUES(99,3,'GET','story',12,'GET','/api/stories/12/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,182,'2026-07-14 13:47:20.990404');
INSERT INTO """audit_logs""" VALUES(100,3,'GET','story',11,'GET','/api/stories/11/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,220,'2026-07-14 13:47:21.027577');
INSERT INTO """audit_logs""" VALUES(101,3,'GET','story',9,'GET','/api/stories/9/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,249,'2026-07-14 13:47:21.056949');
INSERT INTO """audit_logs""" VALUES(102,3,'GET','story',16,'GET','/api/stories/16/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,21,'2026-07-14 13:47:21.125006');
INSERT INTO """audit_logs""" VALUES(103,3,'GET','story',13,'GET','/api/stories/13/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,99,'2026-07-14 13:47:21.188233');
INSERT INTO """audit_logs""" VALUES(104,3,'GET','story',14,'GET','/api/stories/14/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,129,'2026-07-14 13:47:21.217988');
INSERT INTO """audit_logs""" VALUES(105,3,'GET','story',17,'GET','/api/stories/17/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,153,'2026-07-14 13:47:21.250248');
INSERT INTO """audit_logs""" VALUES(106,3,'GET','story',15,'GET','/api/stories/15/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,183,'2026-07-14 13:47:21.280428');
INSERT INTO """audit_logs""" VALUES(107,3,'GET','story',18,'GET','/api/stories/18/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,216,'2026-07-14 13:47:21.312655');
INSERT INTO """audit_logs""" VALUES(108,3,'GET','story',19,'GET','/api/stories/19/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,29,'2026-07-14 13:47:21.380512');
INSERT INTO """audit_logs""" VALUES(109,3,'GET','story',22,'GET','/api/stories/22/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,68,'2026-07-14 13:47:21.427847');
INSERT INTO """audit_logs""" VALUES(110,3,'GET','story',20,'GET','/api/stories/20/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,110,'2026-07-14 13:47:21.468389');
INSERT INTO """audit_logs""" VALUES(111,3,'GET','story',24,'GET','/api/stories/24/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,142,'2026-07-14 13:47:21.501685');
INSERT INTO """audit_logs""" VALUES(112,3,'GET','story',21,'GET','/api/stories/21/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,176,'2026-07-14 13:47:21.535973');
INSERT INTO """audit_logs""" VALUES(113,3,'GET','story',23,'GET','/api/stories/23/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,212,'2026-07-14 13:47:21.572306');
INSERT INTO """audit_logs""" VALUES(114,3,'GET','story',25,'GET','/api/stories/25/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,30,'2026-07-14 13:47:21.638276');
INSERT INTO """audit_logs""" VALUES(115,3,'GET','story',26,'GET','/api/stories/26/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,75,'2026-07-14 13:47:21.676689');
INSERT INTO """audit_logs""" VALUES(116,3,'GET','story',27,'GET','/api/stories/27/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,93,'2026-07-14 13:47:21.705251');
INSERT INTO """audit_logs""" VALUES(117,3,'GET','story',30,'GET','/api/stories/30/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,126,'2026-07-14 13:47:21.738091');
INSERT INTO """audit_logs""" VALUES(118,3,'GET','story',28,'GET','/api/stories/28/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,174,'2026-07-14 13:47:21.786779');
INSERT INTO """audit_logs""" VALUES(119,3,'GET','story',29,'GET','/api/stories/29/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,228,'2026-07-14 13:47:21.840210');
INSERT INTO """audit_logs""" VALUES(120,3,'GET','story',33,'GET','/api/stories/33/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,41,'2026-07-14 13:47:21.962471');
INSERT INTO """audit_logs""" VALUES(121,3,'GET','story',31,'GET','/api/stories/31/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,194,'2026-07-14 13:47:22.087416');
INSERT INTO """audit_logs""" VALUES(122,3,'GET','story',35,'GET','/api/stories/35/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,297,'2026-07-14 13:47:22.204013');
INSERT INTO """audit_logs""" VALUES(123,3,'GET','story',32,'GET','/api/stories/32/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,341,'2026-07-14 13:47:22.241145');
INSERT INTO """audit_logs""" VALUES(124,3,'GET','story',34,'GET','/api/stories/34/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,373,'2026-07-14 13:47:22.279664');
INSERT INTO """audit_logs""" VALUES(125,3,'GET','story',36,'GET','/api/stories/36/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,403,'2026-07-14 13:47:22.311083');
INSERT INTO """audit_logs""" VALUES(126,3,'GET','story',39,'GET','/api/stories/39/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,24,'2026-07-14 13:47:22.378007');
INSERT INTO """audit_logs""" VALUES(127,3,'GET','story',38,'GET','/api/stories/38/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,101,'2026-07-14 13:47:22.450329');
INSERT INTO """audit_logs""" VALUES(128,3,'GET','story',40,'GET','/api/stories/40/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,146,'2026-07-14 13:47:22.495493');
INSERT INTO """audit_logs""" VALUES(129,3,'GET','story',37,'GET','/api/stories/37/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,186,'2026-07-14 13:47:22.534670');
INSERT INTO """audit_logs""" VALUES(130,3,'GET','story',42,'GET','/api/stories/42/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,232,'2026-07-14 13:47:22.581891');
INSERT INTO """audit_logs""" VALUES(131,3,'GET','story',41,'GET','/api/stories/41/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,265,'2026-07-14 13:47:22.623869');
INSERT INTO """audit_logs""" VALUES(132,NULL,'OPTIONS','project',3,'OPTIONS','/api/projects/3','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,1,'2026-07-14 13:47:22.680059');
INSERT INTO """audit_logs""" VALUES(133,3,'GET','story',43,'GET','/api/stories/43/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,60,'2026-07-14 13:47:22.732703');
INSERT INTO """audit_logs""" VALUES(134,3,'GET','story',44,'GET','/api/stories/44/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,101,'2026-07-14 13:47:22.772815');
INSERT INTO """audit_logs""" VALUES(135,3,'GET','project',3,'GET','/api/projects/3','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,82,'2026-07-14 13:47:22.805206');
INSERT INTO """audit_logs""" VALUES(136,NULL,'OPTIONS','project',3,'OPTIONS','/api/projects/3/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,9,'2026-07-14 13:47:22.859030');
INSERT INTO """audit_logs""" VALUES(137,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,45,'2026-07-14 13:47:22.896386');
INSERT INTO """audit_logs""" VALUES(138,NULL,'OPTIONS','project',3,'OPTIONS','/api/projects/3/stats','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,89,'2026-07-14 13:47:22.940860');
INSERT INTO """audit_logs""" VALUES(139,NULL,'OPTIONS','project',3,'OPTIONS','/api/projects/3/sprints','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,145,'2026-07-14 13:47:22.998534');
INSERT INTO """audit_logs""" VALUES(140,NULL,'OPTIONS','project',3,'OPTIONS','/api/projects/3/schedules','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,194,'2026-07-14 13:47:23.054119');
INSERT INTO """audit_logs""" VALUES(141,3,'GET','project',3,'GET','/api/projects/3/sprints','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,109,'2026-07-14 13:47:23.167848');
INSERT INTO """audit_logs""" VALUES(142,3,'GET','project',3,'GET','/api/projects/3/schedules','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,140,'2026-07-14 13:47:23.255536');
INSERT INTO """audit_logs""" VALUES(143,3,'GET','unknown',NULL,'GET','/api/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,251,'2026-07-14 13:47:23.302883');
INSERT INTO """audit_logs""" VALUES(144,3,'GET','project',3,'GET','/api/projects/3/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,340,'2026-07-14 13:47:23.337501');
INSERT INTO """audit_logs""" VALUES(145,3,'GET','project',3,'GET','/api/projects/3/stats','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,334,'2026-07-14 13:47:23.385258');
INSERT INTO """audit_logs""" VALUES(146,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/auth/me','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,1,'2026-07-14 13:47:23.467351');
INSERT INTO """audit_logs""" VALUES(147,3,'GET','unknown',NULL,'GET','/api/auth/me','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,20,'2026-07-14 13:47:23.578380');
INSERT INTO """audit_logs""" VALUES(148,3,'GET','project',3,'GET','/api/projects/3/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,39,'2026-07-14 13:47:23.691605');
INSERT INTO """audit_logs""" VALUES(149,3,'GET','project',3,'GET','/api/projects/3','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,6,'2026-07-14 13:47:31.169126');
INSERT INTO """audit_logs""" VALUES(150,3,'GET','project',3,'GET','/api/projects/3/schedules','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,5,'2026-07-14 13:47:31.218451');
INSERT INTO """audit_logs""" VALUES(151,3,'GET','project',3,'GET','/api/projects/3/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,56,'2026-07-14 13:47:31.268269');
INSERT INTO """audit_logs""" VALUES(152,3,'GET','unknown',NULL,'GET','/api/auth/me','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,3,'2026-07-14 13:47:31.309159');
INSERT INTO """audit_logs""" VALUES(153,3,'GET','project',3,'GET','/api/projects/3/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,12,'2026-07-14 13:47:31.351468');
INSERT INTO """audit_logs""" VALUES(154,NULL,'OPTIONS','project',2,'OPTIONS','/api/projects/2','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,1,'2026-07-14 13:47:32.730883');
INSERT INTO """audit_logs""" VALUES(155,3,'GET','project',2,'GET','/api/projects/2','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,9,'2026-07-14 13:47:32.793903');
INSERT INTO """audit_logs""" VALUES(156,NULL,'OPTIONS','project',2,'OPTIONS','/api/projects/2/sprints','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,0,'2026-07-14 13:47:32.825708');
INSERT INTO """audit_logs""" VALUES(157,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,5,'2026-07-14 13:47:32.858376');
INSERT INTO """audit_logs""" VALUES(158,NULL,'OPTIONS','project',2,'OPTIONS','/api/projects/2/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,39,'2026-07-14 13:47:32.893159');
INSERT INTO """audit_logs""" VALUES(159,NULL,'OPTIONS','project',2,'OPTIONS','/api/projects/2/stats','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,70,'2026-07-14 13:47:32.927378');
INSERT INTO """audit_logs""" VALUES(160,NULL,'OPTIONS','project',2,'OPTIONS','/api/projects/2/schedules','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,119,'2026-07-14 13:47:32.976615');
INSERT INTO """audit_logs""" VALUES(161,3,'GET','unknown',NULL,'GET','/api/tasks','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,46,'2026-07-14 13:47:33.025135');
INSERT INTO """audit_logs""" VALUES(162,3,'GET','project',2,'GET','/api/projects/2/sprints','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,205,'2026-07-14 13:47:33.063132');
INSERT INTO """audit_logs""" VALUES(163,3,'GET','project',2,'GET','/api/projects/2/schedules','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,82,'2026-07-14 13:47:33.093468');
INSERT INTO """audit_logs""" VALUES(164,3,'GET','project',2,'GET','/api/projects/2/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,114,'2026-07-14 13:47:33.122477');
INSERT INTO """audit_logs""" VALUES(165,3,'GET','project',2,'GET','/api/projects/2/stats','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,143,'2026-07-14 13:47:33.153292');
INSERT INTO """audit_logs""" VALUES(166,3,'GET','unknown',NULL,'GET','/api/auth/me','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,5,'2026-07-14 13:47:33.199972');
INSERT INTO """audit_logs""" VALUES(167,3,'GET','project',2,'GET','/api/projects/2/members','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,10,'2026-07-14 13:47:33.241747');
INSERT INTO """audit_logs""" VALUES(168,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/notifications','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,2,'2026-07-14 13:48:00.859010');
INSERT INTO """audit_logs""" VALUES(169,NULL,'OPTIONS','unknown',NULL,'OPTIONS','/api/notifications/unread-count','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,40,'2026-07-14 13:48:00.897991');
INSERT INTO """audit_logs""" VALUES(170,3,'GET','unknown',NULL,'GET','/api/notifications/unread-count','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,25,'2026-07-14 13:48:00.952805');
INSERT INTO """audit_logs""" VALUES(171,3,'GET','unknown',NULL,'GET','/api/notifications','172.17.0.1','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',NULL,200,65,'2026-07-14 13:48:00.991144');
CREATE TABLE comments (
	id INTEGER NOT NULL, 
	task_id INTEGER NOT NULL, 
	author VARCHAR(100) NOT NULL, 
	content TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);
CREATE TABLE """epics""" (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	description TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_epics_status CHECK (status IN ('backlog','todo','in_progress','in_review','verifying','done')), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);
INSERT INTO """epics""" VALUES(1,3,'Epic 1：数据模型与存储层','','done','2026-07-11 15:28:36.447118');
INSERT INTO """epics""" VALUES(2,3,'Epic 2：核心服务层（CRUD）','','done','2026-07-11 15:28:36.535783');
INSERT INTO """epics""" VALUES(3,3,'Epic 3：MCP 服务','','done','2026-07-11 15:28:36.646349');
INSERT INTO """epics""" VALUES(4,3,'Epic 4：OpenSpec / Superpowers 类能力（特色）','','done','2026-07-11 15:28:36.732188');
INSERT INTO """epics""" VALUES(5,3,'Epic 6：以本项目规范管理本项目','','done','2026-07-11 15:28:36.856280');
INSERT INTO """epics""" VALUES(6,3,'Epic 7：前端注册 / 登录（鉴权 UI）','','done','2026-07-11 15:28:36.917491');
INSERT INTO """epics""" VALUES(7,3,'Epic 8：MariaDB 数据库脚本与集成','','done','2026-07-11 15:28:37.011227');
INSERT INTO """epics""" VALUES(8,3,'Epic 9：前端 Web 自动化测试（Playwright）','','done','2026-07-11 15:28:37.104385');
INSERT INTO """epics""" VALUES(9,3,'Epic 10：MCP 鉴权集成与运维化（实现 MCP）','','done','2026-07-11 15:28:37.207680');
INSERT INTO """epics""" VALUES(10,3,'Epic 11：持续前端优化（模仿 Jira，小步迭代）【长期轨道】','','done','2026-07-11 15:28:37.336316');
INSERT INTO """epics""" VALUES(11,3,'Epic 12：轻量 Jira 核心与 Agent 开发闭环（v0.3）','','done','2026-07-11 15:28:37.345573');
INSERT INTO """epics""" VALUES(12,3,'Epic 13：项目管理增强（成员/通知/统计/Admin）','成员管理、通知系统、项目统计、Admin 后台','done','2026-07-12 13:35:03.229240');
INSERT INTO """epics""" VALUES(13,3,'Epic 20：API 增强与批量操作（v0.5）','批量操作、高级搜索、导出功能等 API 增强','done','2026-07-13T13:20:53.989501');
INSERT INTO """epics""" VALUES(14,1,'Epic 26: 前端体验升级 v0.6','','in_progress','2026-07-14 11:11:59');
CREATE TABLE notifications (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	type VARCHAR(30) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	content TEXT DEFAULT '' NOT NULL, 
	is_read BOOLEAN DEFAULT 0 NOT NULL, 
	link VARCHAR(500), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE project_members (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	role VARCHAR(20) DEFAULT 'member' NOT NULL, 
	joined_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
INSERT INTO """project_members""" VALUES(1,4,1,'owner','2026-07-12 13:22:37.322667');
INSERT INTO """project_members""" VALUES(2,3,2,'owner','2026-07-12 13:34:31.732829');
CREATE TABLE """projects""" (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	"""key""" VARCHAR(20), 
	description TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	is_private BOOLEAN DEFAULT 0 NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE ("""key""")
);
INSERT INTO """projects""" VALUES(1,'DevPolit','DEV','自动化任务持续优化的试点项目之一。','2026-07-10 14:01:57.466716',0);
INSERT INTO """projects""" VALUES(2,'ChessPolit','CHESS','自动化任务持续优化的试点项目之一。','2026-07-10 14:01:57.476021',0);
INSERT INTO """projects""" VALUES(3,'AgentBoard','AGB','AgentBoard 自身开发任务（源自 docs/tasks.md）','2026-07-11 15:28:36.432470',0);
INSERT INTO """projects""" VALUES(4,'Test Project','TP','','2026-07-12 13:22:37.309693',0);
CREATE TABLE """sprints""" (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	goal TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	start_date DATETIME, 
	end_date DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_sprints_status CHECK (status IN ('planning','active','completed')), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);
CREATE TABLE """stories""" (
	id INTEGER NOT NULL, 
	epic_id INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	description TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_stories_status CHECK (status IN ('backlog','todo','in_progress','in_review','verifying','done')), 
	FOREIGN KEY(epic_id) REFERENCES epics (id)
);
INSERT INTO """stories""" VALUES(1,1,'Story 1.1 定义数据模型','','done','2026-07-11 15:28:36.456361');
INSERT INTO """stories""" VALUES(2,1,'Story 1.2 存储抽象与切换','','done','2026-07-11 15:28:36.494236');
INSERT INTO """stories""" VALUES(3,2,'Story 2.1 项目树 CRUD','','done','2026-07-11 15:28:36.541075');
INSERT INTO """stories""" VALUES(4,2,'Story 2.2 描述与规范读写','','done','2026-07-11 15:28:36.585408');
INSERT INTO """stories""" VALUES(5,2,'Story 2.3 查询、过滤与搜索','','done','2026-07-11 15:28:36.617549');
INSERT INTO """stories""" VALUES(6,3,'Story 3.1 基础 CRUD 工具','','done','2026-07-11 15:28:36.651722');
INSERT INTO """stories""" VALUES(7,3,'Story 3.2 工具契约与返回结构','','done','2026-07-11 15:28:36.700038');
INSERT INTO """stories""" VALUES(8,4,'Story 4.1 Spec 模板','','done','2026-07-11 15:28:36.737479');
INSERT INTO """stories""" VALUES(9,4,'Story 4.2 规范与任务联动（可选）','','done','2026-07-11 15:28:36.766901');
INSERT INTO """stories""" VALUES(10,4,'Story 5.1 REST API','','done','2026-07-11 15:28:36.799692');
INSERT INTO """stories""" VALUES(11,4,'Story 5.2 简易 Web UI','','done','2026-07-11 15:28:36.825387');
INSERT INTO """stories""" VALUES(12,5,'Story 6.1 规范目录','','done','2026-07-11 15:28:36.862762');
INSERT INTO """stories""" VALUES(13,5,'Story 6.2 首个变更提案','','done','2026-07-11 15:28:36.894644');
INSERT INTO """stories""" VALUES(14,6,'Story 7.1 前端鉴权骨架','','done','2026-07-11 15:28:36.923413');
INSERT INTO """stories""" VALUES(15,6,'Story 7.2 登录 / 注册界面','','done','2026-07-11 15:28:36.957839');
INSERT INTO """stories""" VALUES(16,6,'Story 7.3 应用内用户态','','done','2026-07-11 15:28:36.988477');
INSERT INTO """stories""" VALUES(17,7,'Story 8.1 独立 MariaDB 脚本','','done','2026-07-11 15:28:37.020939');
INSERT INTO """stories""" VALUES(18,7,'Story 8.2 真实集成验证','','done','2026-07-11 15:28:37.044906');
INSERT INTO """stories""" VALUES(19,7,'Story 8.3 集成测试','','done','2026-07-11 15:28:37.089271');
INSERT INTO """stories""" VALUES(20,8,'Story 9.1 测试骨架','','done','2026-07-11 15:28:37.110692');
INSERT INTO """stories""" VALUES(21,8,'Story 9.2 真实交互用例','','done','2026-07-11 15:28:37.142317');
INSERT INTO """stories""" VALUES(22,9,'Story 10.1 MCP 用户管理工具','','done','2026-07-11 15:28:37.214423');
INSERT INTO """stories""" VALUES(23,9,'Story 10.2 Token 透传与运维','','done','2026-07-11 15:28:37.230862');
INSERT INTO """stories""" VALUES(24,9,'Story 10.3 验证与文档','','done','2026-07-11 15:28:37.290053');
INSERT INTO """stories""" VALUES(25,11,'Story 12.1 优先级与评论（本轮）','','done','2026-07-11 15:28:37.367612');
INSERT INTO """stories""" VALUES(26,11,'Story 12.2 Sprint 规划','','done','2026-07-11 15:28:37.481371');
INSERT INTO """stories""" VALUES(27,11,'Story 12.3 附件','','done','2026-07-11 15:28:37.530631');
INSERT INTO """stories""" VALUES(28,11,'Story 12.4 定时 Agent 开发','','done','2026-07-11 15:28:37.582513');
INSERT INTO """stories""" VALUES(29,12,'Story 13.1 成员管理与项目可见性','成员列表/邀请/移除/角色变更；项目 is_private 可见性控制','done','2026-07-12 13:35:03.256761');
INSERT INTO """stories""" VALUES(30,12,'Story 13.2 用户通知系统','通知 CRUD、导航栏铃铛图标、下拉通知面板','done','2026-07-12 13:35:03.269908');
INSERT INTO """stories""" VALUES(31,12,'Story 13.3 项目统计 Tab','项目统计 Tab：总任务/开发中/完成率 + 每日新增/完成任务柱状图','done','2026-07-12 13:35:03.278825');
INSERT INTO """stories""" VALUES(32,12,'Story 13.4 管理员后台','/admin 路由、用户管理（设管理员）、项目管理（删除）','done','2026-07-12 13:35:03.288515');
INSERT INTO """stories""" VALUES(33,13,'Story 20.1 批量任务操作','批量更新状态、批量分配 Sprint、批量删除','done','2026-07-13T13:20:53.989501');
INSERT INTO """stories""" VALUES(34,13,'Story 20.2 高级搜索与过滤 API','支持复杂条件组合搜索、排序参数增强','done','2026-07-13T13:20:53.989501');
INSERT INTO """stories""" VALUES(35,13,'Story 20.3 数据导出功能','导出项目/Epic/Story 数据为 JSON/CSV','done','2026-07-13T13:20:53.989501');
INSERT INTO """stories""" VALUES(36,14,'Story 21.1 健康检查与通知自动轮询','健康检查 60s 轮询（可开关）+ 通知未读数自动轮询 + API 离线检测','done','2026-07-14T01:51:19.023087');
INSERT INTO """stories""" VALUES(37,14,'Story 21.2 API 缓存强化与性能优化','扩展缓存到 stats 端点 + 配置化 TTL + 优化缓存失效逻辑','backlog','2026-07-14T01:51:19.023087');
INSERT INTO """stories""" VALUES(38,14,'Story 21.3 批量操作 UX 增强','批量操作进度指示 + 失败反馈优化 + 批量选择快捷键','backlog','2026-07-14T01:51:19.023087');
INSERT INTO """stories""" VALUES(39,14,'Story 21.4 前端错误处理与离线支持','API 重试机制 + 离线状态提示 + 错误边界','backlog','2026-07-14T01:51:19.023087');
INSERT INTO """stories""" VALUES(40,14,'Story 26.1 看板卡片交互优化','','done','2026-07-14 11:11:59');
INSERT INTO """stories""" VALUES(41,14,'Story 26.2 搜索增强与历史记录','','done','2026-07-14 11:11:59');
INSERT INTO """stories""" VALUES(42,14,'Story 26.3 详情页相邻导航','','done','2026-07-14 11:11:59');
INSERT INTO """stories""" VALUES(43,14,'Story 26.4 性能优化与懒加载','','done','2026-07-14 11:11:59');
INSERT INTO """stories""" VALUES(44,14,'Story 26.5 无障碍访问优化','','done','2026-07-14 11:11:59');
CREATE TABLE task_dependencies (
	id INTEGER NOT NULL, 
	task_id INTEGER NOT NULL, 
	depends_on_id INTEGER NOT NULL, 
	dependency_type VARCHAR(20) DEFAULT 'blocks' NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
	FOREIGN KEY(depends_on_id) REFERENCES tasks (id) ON DELETE CASCADE
);
CREATE TABLE """tasks""" (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	story_id INTEGER, 
	type VARCHAR(10) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	description TEXT NOT NULL, 
	spec TEXT NOT NULL, 
	source_spec_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	priority VARCHAR(10) DEFAULT 'medium' NOT NULL, 
	sprint_id INTEGER, assignee_id INTEGER REFERENCES users(id), due_date DATE, labels TEXT DEFAULT '[]' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_tasks_type CHECK (type IN ('task','bug')), 
	CONSTRAINT ck_tasks_priority CHECK (priority IN ('highest','high','medium','low','lowest')), 
	CONSTRAINT ck_tasks_status CHECK (status IN ('backlog','todo','in_progress','in_review','verifying','done')), 
	CONSTRAINT fk_tasks_source_spec_id_tasks FOREIGN KEY(source_spec_id) REFERENCES tasks (id) ON DELETE SET NULL, 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(story_id) REFERENCES stories (id)
);
INSERT INTO """tasks""" VALUES(1,3,1,'task','绘制 ER 模型并固化到 `docs/requirements.md` §6','done','','',NULL,'2026-07-11 15:28:36.467367','2026-07-11 15:28:36.474850','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(2,3,1,'task','实现 SQLAlchemy 模型（Project / Epic / Story / Task）','done','','',NULL,'2026-07-11 15:28:36.478799','2026-07-11 15:28:36.483475','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(3,3,1,'task','定义状态枚举与合法迁移规则','done','','',NULL,'2026-07-11 15:28:36.486254','2026-07-11 15:28:36.491109','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(4,3,2,'task','基于 `AGENTBOARD_DB_URL` 实现数据库引擎工厂（SQLite / MariaDB）','done','','',NULL,'2026-07-11 15:28:36.503600','2026-07-11 15:28:36.509211','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(5,3,2,'task','编写连接池与健康检查','done','','',NULL,'2026-07-11 15:28:36.512599','2026-07-11 15:28:36.517359','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(6,3,2,'task','Alembic 初始化与首版迁移脚本','done','','',NULL,'2026-07-11 15:28:36.520334','2026-07-11 15:28:36.525374','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(7,3,2,'task','存储层单元测试（SQLite 下跑通，验证 MariaDB DDL 兼容）','done','','',NULL,'2026-07-11 15:28:36.528438','2026-07-11 15:28:36.533342','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(8,3,3,'task','`create_project / get_project / list_projects / update_project / delete_project`','done','','',NULL,'2026-07-11 15:28:36.548493','2026-07-11 15:28:36.553855','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(9,3,3,'task','`create_epic / get_epic / list_epics`','done','','',NULL,'2026-07-11 15:28:36.556471','2026-07-11 15:28:36.560789','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(10,3,3,'task','`create_story / get_story / list_stories`','done','','',NULL,'2026-07-11 15:28:36.563449','2026-07-11 15:28:36.567818','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(11,3,3,'task','`create_task`（type=task|bug）/ `get_task` / `list_tasks`','done','','',NULL,'2026-07-11 15:28:36.570492','2026-07-11 15:28:36.575106','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(12,3,3,'task','级联删除策略（Project 删除时处理子节点）','done','','',NULL,'2026-07-11 15:28:36.578071','2026-07-11 15:28:36.582993','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(13,3,4,'task','`set_task_description` / `get_task_description`','done','','',NULL,'2026-07-11 15:28:36.591357','2026-07-11 15:28:36.599474','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(14,3,4,'task','`set_task_spec` / `get_task_spec` / `append_task_spec`','done','','',NULL,'2026-07-11 15:28:36.602381','2026-07-11 15:28:36.607028','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(15,3,4,'task','spec 内容校验（markdown 解析不报错即接受）','done','','',NULL,'2026-07-11 15:28:36.610518','2026-07-11 15:28:36.615064','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(16,3,5,'task','按 project / epic / story / type / status 过滤','done','','',NULL,'2026-07-11 15:28:36.623863','2026-07-11 15:28:36.628947','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(17,3,5,'task','关键字搜索 title / description / spec','done','','',NULL,'2026-07-11 15:28:36.631614','2026-07-11 15:28:36.636242','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(18,3,5,'task','服务层单元测试','done','','',NULL,'2026-07-11 15:28:36.639195','2026-07-11 15:28:36.643853','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(19,3,6,'task','FastMCP 服务骨架 + 启动入口','done','','',NULL,'2026-07-11 15:28:36.658740','2026-07-11 15:28:36.663869','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(20,3,6,'task','注册项目树 CRUD 工具','done','','',NULL,'2026-07-11 15:28:36.667281','2026-07-11 15:28:36.672490','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(21,3,6,'task','注册 task 描述 / spec 读写工具','done','','',NULL,'2026-07-11 15:28:36.676288','2026-07-11 15:28:36.680965','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(22,3,6,'task','注册搜索 / 过滤工具','done','','',NULL,'2026-07-11 15:28:36.684504','2026-07-11 15:28:36.689376','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(23,3,6,'task','注册状态流转工具（校验合法迁移）','done','','',NULL,'2026-07-11 15:28:36.693175','2026-07-11 15:28:36.697748','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(24,3,7,'task','统一 JSON 返回结构（含 id / 错误信息）','done','','',NULL,'2026-07-11 15:28:36.709969','2026-07-11 15:28:36.715153','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(25,3,7,'task','MCP 工具入参 schema 校验','done','','',NULL,'2026-07-11 15:28:36.717942','2026-07-11 15:28:36.722755','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(26,3,7,'task','本地以 MCP 客户端联调（SQLite）','done','','',NULL,'2026-07-11 15:28:36.725474','2026-07-11 15:28:36.729853','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(30,3,9,'task','从 spec 解析清单项（- [ ] 标题）并批量建同级子 task（generate_tasks_from_spec）','done','','',NULL,'2026-07-11 15:28:36.773568','2026-07-11 15:28:36.779076','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(31,3,9,'task','task 与 spec 双向引用（子任务记录 source_spec_id，源 spec 回写链接）','done','','',NULL,'2026-07-11 15:28:36.781991','2026-07-11 15:28:36.786624','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(32,3,9,'task','状态联动（spec 进入 review 时关联 task 转 in_review）','done','','',NULL,'2026-07-11 15:28:36.789587','2026-07-11 15:28:36.789593','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(33,3,10,'task','FastAPI 暴露核心 CRUD','done','','',NULL,'2026-07-11 15:28:36.807507','2026-07-11 15:28:36.807514','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(34,3,10,'task','与 MCP 共用同一 service 层','done','','',NULL,'2026-07-11 15:28:36.818973','2026-07-11 15:28:36.818980','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(35,3,11,'task','项目树浏览','done','','',NULL,'2026-07-11 15:28:36.831842','2026-07-11 15:28:36.831848','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(36,3,11,'task','任务详情 + markdown 渲染（description / spec）','done','','',NULL,'2026-07-11 15:28:36.840253','2026-07-11 15:28:36.840260','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(37,3,11,'task','状态切换交互','done','','',NULL,'2026-07-11 15:28:36.848970','2026-07-11 15:28:36.848978','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(38,3,12,'task','建立 `openspec/specs/agentboard/spec.md`（能力规格 = 当前事实来源）','done','','',NULL,'2026-07-11 15:28:36.869419','2026-07-11 15:28:36.874649','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(39,3,12,'task','建立 `openspec/changes/` 目录 + `openspec/AGENTS.md`（Agent 指引）','done','','',NULL,'2026-07-11 15:28:36.877964','2026-07-11 15:28:36.882895','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(40,3,12,'task','将本 `docs/tasks.md` 与 OpenSpec `tasks.md` 对齐（后续变更走 `openspec/changes/*/tasks.md`）','done','','',NULL,'2026-07-11 15:28:36.885909','2026-07-11 15:28:36.890672','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(41,3,13,'task','以 OpenSpec 提案形式描述后续变更（见 `openspec/changes/mariadb-alembic/`）','done','','',NULL,'2026-07-11 15:28:36.900777','2026-07-11 15:28:36.906643','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(42,3,13,'task','MariaDB 接入 + Alembic 迁移 + MCP 工具补全（进行中，见 change 的 tasks.md）','done','','',NULL,'2026-07-11 15:28:36.911019','2026-07-11 15:28:36.911029','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(43,3,14,'task','`app.js` 增加 `getToken/setToken/clearToken`（localStorage）','done','','',NULL,'2026-07-11 15:28:36.933259','2026-07-11 15:28:36.933265','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(44,3,14,'task','改造 `api()` 自动注入 `Authorization`；收到 401 清 token 回登录','done','','',NULL,'2026-07-11 15:28:36.940427','2026-07-11 15:28:36.940434','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(45,3,14,'task','`index.html` 预留登录 / 注册容器','done','','',NULL,'2026-07-11 15:28:36.949634','2026-07-11 15:28:36.949643','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(46,3,15,'task','`renderAuth()`：用户名 / 密码 + 登录/注册切换，调用 `/api/auth/register|login`','done','','',NULL,'2026-07-11 15:28:36.964417','2026-07-11 15:28:36.964423','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(47,3,15,'task','成功存 token 进应用；失败（409/401）展示错误','done','','',NULL,'2026-07-11 15:28:36.971713','2026-07-11 15:28:36.971719','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(48,3,15,'task','启动守卫：有 token 且 `/api/auth/me` 通过则进应用，否则显示登录','done','','',NULL,'2026-07-11 15:28:36.980320','2026-07-11 15:28:36.980327','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(49,3,16,'task','顶部栏显示当前用户名 + 登出按钮','done','','',NULL,'2026-07-11 15:28:36.995984','2026-07-11 15:28:36.995991','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(50,3,16,'task','`style.css` 补充登录卡片 / 用户信息条样式','done','','',NULL,'2026-07-11 15:28:37.003351','2026-07-11 15:28:37.003359','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(51,3,17,'task','新增 `scripts/mariadb/schema.sql`（建库 utf8mb4、建用户授权、五表与 `models.py` 对齐、含 `source_spec_id`）','done','','',NULL,'2026-07-11 15:28:37.028236','2026-07-11 15:28:37.028243','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(52,3,17,'task','`scripts/mariadb/README.md` 说明初始化与离线评审用法','done','','',NULL,'2026-07-11 15:28:37.036330','2026-07-11 15:28:37.036341','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(53,3,18,'task','用户提供 MariaDB 连接信息（`AGENTBOARD_DB_URL=mysql+pymysql://...`）','done','','',NULL,'2026-07-11 15:28:37.054430','2026-07-11 15:28:37.054439','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(54,3,18,'task','验证 Alembic `upgrade head` 在 MariaDB 11 建表 DDL 兼容','done','','',NULL,'2026-07-11 15:28:37.065836','2026-07-11 15:28:37.065846','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(55,3,18,'task','MariaDB 下跑通 service 层冒烟（CRUD + 状态机 + 搜索 + 生成子任务）','done','','',NULL,'2026-07-11 15:28:37.075400','2026-07-11 15:28:37.075407','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(56,3,18,'task','更新 `docker-compose.yml` 的 `db` profile 与 API 对接示例','done','','',NULL,'2026-07-11 15:28:37.082602','2026-07-11 15:28:37.082609','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(57,3,19,'task','新增 `tests/test_mariadb_integration.py`（`skipif` 无 `AGENTBOARD_TEST_MARIADB`）','done','','',NULL,'2026-07-11 15:28:37.096835','2026-07-11 15:28:37.096843','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(58,3,20,'task','`requirements.txt` 增加 `playwright` / `pytest-playwright`','done','','',NULL,'2026-07-11 15:28:37.117112','2026-07-11 15:28:37.117119','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(59,3,20,'task','新增 `tests/test_playwright_e2e.py`：fixture 启动真实 API + Web（临时 SQLite）','done','','',NULL,'2026-07-11 15:28:37.125876','2026-07-11 15:28:37.125883','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(60,3,20,'task','UI 辅助函数 `ui_register / ui_login`','done','','',NULL,'2026-07-11 15:28:37.135473','2026-07-11 15:28:37.135481','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(61,3,21,'task','注册 UI 流（进入应用 + localStorage 含 token）','done','','',NULL,'2026-07-11 15:28:37.154187','2026-07-11 15:28:37.154195','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(62,3,21,'task','登录 UI 流 + 错误密码 / 重复注册报错','done','','',NULL,'2026-07-11 15:28:37.162878','2026-07-11 15:28:37.162884','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(63,3,21,'task','项目树 CRUD UI（Project→Epic→Story→Task/Bug）','done','','',NULL,'2026-07-11 15:28:37.171272','2026-07-11 15:28:37.171280','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(64,3,21,'task','状态流转 UI（徽标更新）','done','','',NULL,'2026-07-11 15:28:37.179123','2026-07-11 15:28:37.179130','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(65,3,21,'task','spec 编辑与 markdown 渲染','done','','',NULL,'2026-07-11 15:28:37.186503','2026-07-11 15:28:37.186509','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(66,3,21,'task','README 补充 `playwright install chromium` 与运行命令','done','','',NULL,'2026-07-11 15:28:37.195375','2026-07-11 15:28:37.195385','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(67,3,22,'task','`mcp_server.py` 新增 `auth_register` / `auth_login` / `auth_me`（api + db 双后端）','done','','',NULL,'2026-07-11 15:28:37.221774','2026-07-11 15:28:37.227661','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(68,3,23,'task','`api` 后端透传当前远程 MCP Token，stdio 回退 `AGENTBOARD_MCP_TOKEN`','done','','',NULL,'2026-07-11 15:28:37.237588','2026-07-11 15:28:37.244093','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(69,3,23,'task','`python -m agentboard.mcp_server` 支持 stdio/http 环境配置','done','','',NULL,'2026-07-11 15:28:37.249051','2026-07-11 15:28:37.255272','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(70,3,23,'task','客户端配置样例 `examples/mcp-stdio.json` / `examples/mcp-remote.json`','done','','',NULL,'2026-07-11 15:28:37.259902','2026-07-11 15:28:37.265801','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(71,3,23,'task','完整 Project/Epic/Story/Task list/get/update/delete 工具','done','','',NULL,'2026-07-11 15:28:37.270263','2026-07-11 15:28:37.275842','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(72,3,23,'task','Docker Compose 远程 MCP 服务与 REST 强制鉴权','done','','',NULL,'2026-07-11 15:28:37.281626','2026-07-11 15:28:37.287274','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(73,3,24,'task','新增 `tests/test_mcp_smoke.py`（真实 HTTP、Bearer、API Token 透传、完整项目树）','done','','',NULL,'2026-07-11 15:28:37.298389','2026-07-11 15:28:37.304892','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(74,3,24,'task','README「MCP 运行与接入」章节','done','','',NULL,'2026-07-11 15:28:37.309739','2026-07-11 15:28:37.315742','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(75,3,24,'task','更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单','done','','',NULL,'2026-07-11 15:28:37.321157','2026-07-11 15:28:37.332282','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(76,3,25,'task','任务增加五级 `priority`，支持创建、编辑、筛选及迁移','done','','',NULL,'2026-07-11 15:28:37.379216','2026-07-11 15:28:37.388992','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(77,3,25,'task','评论表与服务层 CRUD，删除任务/父级时同步清理','done','','',NULL,'2026-07-11 15:28:37.397550','2026-07-11 15:28:37.412763','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(78,3,25,'task','REST API 暴露优先级与评论端点','done','','',NULL,'2026-07-11 15:28:37.417825','2026-07-11 15:28:37.425645','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(79,3,25,'task','MCP 支持设置/筛选优先级、添加/读取/删除评论','done','','',NULL,'2026-07-11 15:28:37.431268','2026-07-11 15:28:37.442759','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(80,3,25,'task','Web 任务列表/详情显示优先级，详情页支持评论流','done','','',NULL,'2026-07-11 15:28:37.449512','2026-07-11 15:28:37.457182','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(81,3,25,'task','补充服务、REST 与 MCP 回归测试','done','','',NULL,'2026-07-11 15:28:37.468842','2026-07-11 15:28:37.475113','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(82,3,26,'task','Sprint 数据模型、迁移、状态机与“单 active Sprint”约束','done','','',NULL,'2026-07-11 15:28:37.492844','2026-07-11 15:28:37.492854','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(83,3,26,'task','Sprint CRUD、任务入 Sprint、关闭时搬迁未完成任务','done','','',NULL,'2026-07-11 15:28:37.505410','2026-07-11 15:28:37.505420','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(84,3,26,'task','Sprint/Backlog Web 视图与 MCP 工具','done','','',NULL,'2026-07-11 15:28:37.518846','2026-07-11 15:28:37.518856','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(85,3,27,'task','附件元数据模型、本地安全存储与大小/MIME 限制','done','','',NULL,'2026-07-11 15:28:37.546909','2026-07-11 15:28:37.546919','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(86,3,27,'task','上传、列表、下载、删除 REST API','done','','',NULL,'2026-07-11 15:28:37.560753','2026-07-11 15:28:37.560769','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(87,3,27,'task','任务详情附件区与 MCP 资源信息工具','done','','',NULL,'2026-07-11 15:28:37.572117','2026-07-11 15:28:37.572127','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(88,3,28,'task','AgentSchedule / AgentRun 模型、一次性与 cron 表达式校验','done','','',NULL,'2026-07-11 15:28:37.597062','2026-07-11 15:28:37.597070','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(89,3,28,'task','带租约和幂等键的调度扫描器，避免重复运行','done','','',NULL,'2026-07-11 15:28:37.608973','2026-07-11 15:28:37.608980','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(90,3,28,'task','Codex / WorkBuddy / Qoder 执行器适配契约与最小安全策略','done','','',NULL,'2026-07-11 15:28:37.617643','2026-07-11 15:28:37.617651','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(91,3,28,'task','Web 计划配置、运行历史、失败重试与停用入口','done','','',NULL,'2026-07-11 15:28:37.627487','2026-07-11 15:28:37.627495','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(92,3,28,'task','MCP 提供领取任务、心跳、状态/评论同步与运行完成工具','done','','',NULL,'2026-07-11 15:28:37.635926','2026-07-11 15:28:37.635934','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(93,3,29,'task','Task 93 数据模型：新增 is_private/is_admin/ProjectMember/Notification','done','Project 新增 is_private；User 新增 is_admin；新增 ProjectMember 表（project_id/user_id/role）；新增 Notification 表（type/title/content/is_read/link）','',NULL,'2026-07-12 13:35:03.299357','2026-07-12 13:35:03.299362','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(94,3,29,'task','Task 94 后端 API：成员管理与项目可见性过滤','done','GET/POST /api/projects/{pid}/members；DELETE /api/projects/{pid}/members/{uid}；PATCH 变更角色；项目列表按可见性过滤；创建项目自动分配 owner','',NULL,'2026-07-12 13:35:03.312925','2026-07-12 13:35:03.312934','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(95,3,29,'task','Task 95 前端 Members Tab','done','Members Tab：成员列表、邀请表单（用户名+角色）、移除、角色变更（Owner/Member）；is_private 字段在 Settings Tab 编辑','',NULL,'2026-07-12 13:35:03.328033','2026-07-12 13:35:03.328038','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(96,3,30,'task','Task 96 后端 API：通知系统','done','GET /api/notifications（支持 unread_only）；GET /api/notifications/unread-count；POST /api/notifications/{nid}/read；POST /api/notifications/read-all；DELETE /api/notifications/{nid}；create_project 时发送 project_invite 通知','',NULL,'2026-07-12 13:35:03.340532','2026-07-12 13:35:03.340539','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(97,3,30,'task','Task 97 前端通知面板','done','导航栏铃铛图标 + 未读计数徽章 + 下拉面板；通知列表；点击标记已读；全部已读；删除通知','',NULL,'2026-07-12 13:35:03.354289','2026-07-12 13:35:03.354295','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(98,3,31,'task','Task 98 后端 API：项目统计数据','done','GET /api/projects/{pid}/stats：每日新增任务（最近30天）；每日完成任务；总任务数/开发中/Backlog/完成率','',NULL,'2026-07-12 13:35:03.369519','2026-07-12 13:35:03.369526','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(99,3,31,'task','Task 99 前端 Stats Tab','done','Stats Tab：5个统计卡片（总任务/开发中/Backlog/已完成/完成率）；每日新增柱状图；每日完成柱状图（最近30天）','',NULL,'2026-07-12 13:35:03.381232','2026-07-12 13:35:03.381239','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(100,3,32,'task','Task 100 后端 API：管理员后台','done','GET /api/admin/users + PATCH 设管理员；GET /api/admin/projects + DELETE 删除项目；首个注册用户自动成为管理员；仅 is_admin=true 可访问','',NULL,'2026-07-12 13:35:03.392703','2026-07-12 13:35:03.392709','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(101,3,32,'task','Task 101 前端 Admin 视图','done','/admin 路由；用户管理表格（设管理员/撤销）；项目管理表格（查看/删除）；导航栏 Admin 专属入口（⚙）；is_admin localStorage 持久化','',NULL,'2026-07-12 13:35:03.406672','2026-07-12 13:35:03.406678','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(102,3,32,'task','Task 102 MCP 工具补全','backlog','将新增 API（成员管理/通知/统计/管理员）补全到 mcp_server.py 的 MCP 工具列表','',NULL,'2026-07-12 13:35:03.419607','2026-07-12 13:35:03.419613','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(103,3,33,'task','批量更新任务状态 API','done','POST /api/tasks/batch-update-status，接收任务ID列表和新状态','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(104,3,33,'task','批量分配 Sprint API','done','POST /api/tasks/batch-assign-sprint，接收任务ID列表和sprint_id','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(105,3,33,'task','批量删除任务 API','done','POST /api/tasks/batch-delete，接收任务ID列表','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(106,3,34,'task','增强排序参数','done','支持按 created_at/updated_at/priority/status 排序，支持 ASC/DESC','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(107,3,34,'task','多条件组合过滤 API','done','GET /api/tasks 支持 status[]=xx&priority[]=xx 多值过滤','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','high',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(108,3,35,'task','导出项目数据 API','done','GET /api/projects/{pid}/export 返回完整项目树 JSON','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(109,3,35,'task','导出 Epic/Story 数据','done','支持导出 Epic 或 Story 及其所有子任务','',NULL,'2026-07-13T13:20:53.989501','2026-07-13T13:20:53.989501','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(110,3,36,'task','Task 400: 健康检查定时轮询（60s）+ 可开关存储在 localStorage','done','Task 400: 健康检查定时轮询（60s）+ 可开关存储在 localStorage','',NULL,'2026-07-14T01:42:16.088015','2026-07-14T01:42:16.088015','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(111,3,36,'task','Task 401: 通知未读数自动轮询（60s）+ 面板打开时立即刷新','done','Task 401: 通知未读数自动轮询（60s）+ 面板打开时立即刷新','',NULL,'2026-07-14T01:42:16.088015','2026-07-14T01:42:16.088015','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(112,3,36,'task','Task 402: API 离线检测：网络断开时显示离线提示条','done','Task 402: API 离线检测：网络断开时显示离线提示条','',NULL,'2026-07-14T01:42:16.088015','2026-07-14T01:42:16.088015','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(700,1,40,'task','Task 700: 看板卡片 hover 动画增强','done','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(701,1,40,'task','Task 701: 看板列拖拽占位符动画','backlog','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(702,1,41,'task','Task 702: 搜索框历史记录下拉','done','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(703,1,41,'task','Task 703: 搜索结果高亮关键词','done','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(704,1,42,'task','Task 704: 任务详情页上一条/下一条导航','done','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(705,1,43,'task','Task 705: API 响应缓存与防抖','backlog','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(706,1,44,'task','Task 706: 关键元素 ARIA 属性添加','done','','',NULL,'2026-07-14 11:11:59','2026-07-14 11:11:59','medium',NULL,NULL,NULL,'[]');
INSERT INTO """tasks""" VALUES(707,3,45,'task','Task 9.3.1: 新增 tests/test_web_assets_e2e.py 8 项契约测试','in_review','8/8 PASSED：test_index_html_served / test_index_html_has_app_root / test_no_404_on_home_resources / test_angular_actually_boots / test_angular_renders_known_text / test_js_files_have_correct_mime / test_css_files_have_correct_mime / test_index_html_resource_paths_resolve','新增测试文件 tests/test_web_assets_e2e.py（191 行）。覆盖：首页 200、app-root 存在、无 404 资源、Angular 启动、品牌文案、JS/CSS MIME 类型、index.html 资源路径解析。',NULL,'2026-07-14T21:10:38.134255','2026-07-14T21:10:38.134255','medium',NULL,NULL,NULL,'[]');
CREATE TABLE """users""" (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	created_at DATETIME NOT NULL, 
	is_admin BOOLEAN DEFAULT 0 NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
INSERT INTO """users""" VALUES(1,'testadmin','pbkdf2_sha256$600000$1a4639e70d8d5f93140f7e50c90d9f97$eca73fadd95ef05b705a0a544833e634a06c98ff1421fb236299ff1b8a7c1869','2026-07-12 13:22:37.291860',1);
INSERT INTO """users""" VALUES(2,'ticket_creator','pbkdf2_sha256$600000$2a300dbd8b7c22771c5a01db322f54e2$f83754966e222f56afd17136bc2bcfb5d9e2c84272cced001583310f7da053c2','2026-07-12 13:34:22.321478',1);
INSERT INTO """users""" VALUES(3,'jzhong2026','pbkdf2_sha256$600000$d3805dd56097976c85a37f1499fea81a$da082ab86478dd29d3d506e126d9bf6280690d97fa53b1e646a8497e1ed1952c','2026-07-14 13:47:12.179671',0);
CREATE TABLE webhook_configs (
	id INTEGER NOT NULL, 
	project_id INTEGER, 
	name VARCHAR(100) NOT NULL, 
	url VARCHAR(2000) NOT NULL, 
	secret VARCHAR(256), 
	events TEXT DEFAULT '[]' NOT NULL, 
	enabled BOOLEAN DEFAULT 1 NOT NULL, 
	created_by INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_comments_task_id ON comments (task_id);
CREATE INDEX ix_epics_project_id ON epics (project_id);
CREATE INDEX ix_stories_epic_id ON stories (epic_id);
CREATE INDEX ix_sprints_project_id ON sprints (project_id);
CREATE INDEX ix_tasks_sprint_id ON tasks (sprint_id);
CREATE INDEX ix_tasks_project_id ON tasks (project_id);
CREATE INDEX ix_tasks_story_id ON tasks (story_id);
CREATE INDEX ix_tasks_source_spec_id ON tasks (source_spec_id);
CREATE INDEX ix_attachments_task_id ON attachments (task_id);
CREATE INDEX ix_agent_schedules_project_id ON agent_schedules (project_id);
CREATE INDEX ix_agent_runs_schedule_id ON agent_runs (schedule_id);
CREATE INDEX ix_agent_runs_task_id ON agent_runs (task_id);
CREATE INDEX ix_project_members_user_id ON project_members (user_id);
CREATE INDEX ix_project_members_project_id ON project_members (project_id);
CREATE UNIQUE INDEX ix_project_members_unique ON project_members (project_id, user_id);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);
CREATE INDEX ix_tasks_project_status ON tasks (project_id, status);
CREATE INDEX ix_tasks_project_priority ON tasks (project_id, priority);
CREATE INDEX ix_tasks_status ON tasks (status);
CREATE INDEX ix_epics_project_status ON epics (project_id, status);
CREATE INDEX ix_stories_epic_status ON stories (epic_id, status);
CREATE INDEX ix_sprints_project_status ON sprints (project_id, status);
CREATE INDEX ix_api_keys_user_id ON api_keys (user_id);
CREATE INDEX ix_api_keys_key_prefix ON api_keys (key_prefix);
CREATE INDEX ix_audit_logs_action ON audit_logs (action);
CREATE INDEX ix_audit_logs_entity_id ON audit_logs (entity_id);
CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);
CREATE INDEX ix_task_dependencies_task_id ON task_dependencies (task_id);
CREATE INDEX ix_task_dependencies_depends_on_id ON task_dependencies (depends_on_id);
CREATE INDEX ix_webhook_configs_project_id ON webhook_configs (project_id);
CREATE INDEX ix_webhook_configs_enabled ON webhook_configs (enabled);
COMMIT;
