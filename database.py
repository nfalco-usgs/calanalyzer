#!/usr/bin/env python

#############################################################################
#	database.py																#
#																			#
#	Author:		Adam Baker (ambaker@usgs.gov)								#
#	Date:		2016-05-11													#
#	Version:	0.3.5														#
#																			#
#	Purpose:	Allows for quicker implementation of a database				#
#############################################################################

import psycopg2

class Database(object):
	def __init__(self, dbname = None, user = None, host = None, password = None):
		'Initializes the connection to the database'
		self._dbname = dbname
		self._user = user
		self._host = host
		self._password = password
		self.open_connection(self._dbname, self._user, self._host, self._password)
	def open_connection(self, dbname = None, user = None, host = None, password = None):
		'Opens the connection to the database'
		if dbname != None: self._dbname = dbname
		if user != None: self._user = user
		if host != None: self._host = host
		if password != None: self._password = password
		self.conn = psycopg2.connect("dbname='%s' user='%s' host='%s' password='%s'" % (self._dbname, self._user, self._host, self._password))
	def select_query(self, query):
		'Queries the database with the given PostreSQL query'
		cur = self.conn.cursor()
		cur.execute(query)
		results = cur.fetchall()
		cur.close()
		return results
	def close_connection(self):
		'Closes the connection to the database'
		try:
			self.conn.close()
		except:
			pass