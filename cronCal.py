#!/usr/bin/env python

import glob
import multical
from obspy.core import UTCDateTime

jday = UTCDatetTime.today().julday

#get first year
years = []
paths = glob.glob('/xs[01]/seed/*/*')
for path in path:
	if path.split('/')[-1] not in years:
		if path.split('/')[-1].isdigit():
			years.append(path.split('/')[-1])
yearFirst = years.sort()[0]

print yearFirst

#first, check one year
# yearOne = 
print 'python multical.py -b '

#second, check one month last year

#third, check last full month

#fourth, check one week ago