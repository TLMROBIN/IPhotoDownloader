#!/usr/bin/env python
# _*_ coding=utf8 _*_
import re
import json
import types
import urllib
import base64
import os
import binascii
import sys
import rsa
import requests
import threading
import shutil
import time

import logging
from weibo_conf import UID, THREAD, USER_ID, USER_PWD
#logging.basicConfig(level=logging.DEBUG)


WBCLIENT = 'ssologin.js(v1.4.5)'
user_agent = (
    'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/536.11 (KHTML, like Gecko) '
    'Chrome/20.0.1132.57 Safari/536.11'
)
session = requests.session()
session.headers['User-Agent'] = user_agent
ALBUM_ID = -1
mylock = threading.RLock()
retry_list = []

def encrypt_passwd(passwd, pubkey, servertime, nonce):
    key = rsa.PublicKey(int(pubkey, 16), int('10001', 16))
    message = str(servertime) + '\t' + str(nonce) + '\n' + str(passwd)
    passwd = rsa.encrypt(message, key)
    return binascii.b2a_hex(passwd)


def wblogin(username, password):
    resp = session.get(
        'http://login.sina.com.cn/sso/prelogin.php?'
        'entry=sso&callback=sinaSSOController.preloginCallBack&'
        'su=%s&rsakt=mod&client=%s' %
        (base64.b64encode(username), WBCLIENT)
    )

    pre_login_str = re.match(r'[^{]+({.+?})', resp.content).group(1)
    pre_login = json.loads(pre_login_str)

    #   pre_login = json.loads(pre_login_str)
    data = {
        'entry': 'weibo',
        'gateway': 1,
        'from': '',
        'savestate': 7,
        'userticket': 1,
        'ssosimplelogin': 1,
        'su': base64.b64encode(urllib.quote(username)),
        'service': 'miniblog',
        'servertime': pre_login['servertime'],
        'nonce': pre_login['nonce'],
        'vsnf': 1,
        'vsnval': '',
        'pwencode': 'rsa2',
        'sp': encrypt_passwd(password, pre_login['pubkey'],
                             pre_login['servertime'], pre_login['nonce']),
        'rsakv': pre_login['rsakv'],
        'encoding': 'UTF-8',
        'prelt': '115',
        'url': 'http://weibo.com/ajaxlogin.php?framelogin=1&callback=parent.si'
               'naSSOController.feedBackUrlCallBack',
        'returntype': 'META'
    }
    resp = session.post(
        'http://login.sina.com.cn/sso/login.php?client=%s' % WBCLIENT,
        data=data
    )

    login_url = re.search(r'replace\([\"\']([^\'\"]+)[\"\']',
                          resp.content).group(1)
    resp = session.get(login_url)
    login_str = re.match(r'[^{]+({.+?}})', resp.content).group(1)
    #return json.loads(login_str)
    for uid in UID:
        print '========================================================================='
        print 'Downloading %s\'s photos' % uid
        get_album_id(uid)
        picname_list = []
        page = 1

        sort_dir = str(uid)
        if not os.path.exists('./' + sort_dir):
            os.mkdir('./' + sort_dir)        
        id_list = get_idlist(sort_dir)
        isFirsttime = False
        if len(id_list) == 0:
            isFirsttime = True
            pass
        else:
            id_list = [ids.strip() for ids in id_list]
       
        print 'Parsing...'
        print 'page ',
        while isFirsttime or page < 5:
            print ' %2d' % page,

            des_url = 'http://photo.weibo.com/photos/get_all?uid=%s&album_id=%s&count=30&page=%s&type=3' % (
                uid, ALBUM_ID, page)
            resp = session.get(des_url)
            rep_data = resp.json()['data']['photo_list']
            if len(rep_data) == 0:
                break
            for each in rep_data:

                caption = each['caption_render']
                createdtime = each['created_at']
                picname = each['pic_name']
                pichost = each['pic_host']
                fmt = picname.split('.')[-1]
                if not fmt in ['jpg', 'gif', 'png', 'bmp', 'jpeg', 'tif']:
                    picname = picname + '.jpg'

                if picname in id_list:
                    continue

                if re.search(ur'分钟', createdtime) or re.search(ur'今天', createdtime):
                    createdtime = time.strftime('%Y%m%d', time.localtime(time.time()))
                elif re.search(ur'月', createdtime):
                    m = re.search(ur'([0-9]+)月([0-9]+)日', createdtime)
                    createdtime = '%s%02d%02d'%(time.strftime('%Y', time.localtime(time.time())),
                                            int(m.group(1)),
                                            int(m.group(2))
                                            )
                else:
                    createdtime = createdtime.replace('-','')

                pic = (createdtime, caption, picname, pichost)
                try:
                    if pic not in picname_list:
                        picname_list.append(pic)
                except:
                    pass
            page += 1

        print ' '
        print '%s new photos has been found since last update! ' % len(picname_list)
        # 如果没有新增数据则直接返回进行下一次循环
        if len(picname_list) == 0:
            continue

        # 传入整个list的大小，如果大于配置文件中的数值则按THREAD分片，否则按照list大小分片
        picname_list_div = div_list(picname_list)
        thread = []
        times = len(picname_list_div)
        for i in range(times):
            thread.append(dojob(download, picname_list_div, sort_dir, i))
        for each in thread:
            each.start()
        for each in thread:
            each.join()

        done_list = list(set(picname_list) - set(retry_list))
        set_idlist(sort_dir, list(done_list))

        while len(retry_list) != 0:
            print 'Now retrying download the [ %s ] failed task!!!' % len(retry_list)
            threads = []
            # 分多线程进行重试
            picname_list_div = []
            picname_list_div = div_list(retry_list)
            for i in range(len(picname_list_div)):
                t = threading.Thread(target=retry_download, args=(list(picname_list_div[i]), sort_dir))
                threads.append(t)

            for i in threads:
                i.start()
            for i in threads:
                i.join()

        else:
            pass
        print 'All %s \'s download job has been done.' % uid


class dojob(threading.Thread):
    def __init__(self, func, picname_list, sort_dir, index):
        threading.Thread.__init__(self)
        self.func = func
        self.picname_list = picname_list
        self.sort_dir = sort_dir
        self.index = index

    def run(self):
        self.func(self.picname_list, self.sort_dir, self.index)


def div_list(picname_list):
    '''divide the list into small lists, if sum < THREAD then divide by sum
    '''
    sum = len(picname_list)
    global THREAD
    if sum < THREAD:
        THREAD = sum
    size = int((len(picname_list) + THREAD -1) / THREAD) # round up
    l = [picname_list[i:i + size] for i in range(0, len(picname_list), size)]

    return l


def download(picname_list, sort_dir, index):
    for (createdtime, caption, picname, pichost) in picname_list[index]:

        download_url = '%s/large/%s' % (pichost, picname)
        if len(caption) > 168:
            caption = caption[0:167] + u'…'
        filename = createdtime + '.' + caption +'.' + picname
        filename = clean_filename(filename)

        try:
            urllib.urlretrieve(download_url, './' + sort_dir + '/' + filename)
        except Exception,e:
            print e
            mylock.acquire()
            retry_list.append((createdtime, caption, picname, pichost))
            mylock.release()
            sys.stderr.write('%s  download failed, add to retry queue!' % picname)
            continue
        print 'Download ' + picname + ' successful.'


def retry_download(picname_list, sort_dir):
    '''retry to download the failed task untile all the image has been dowload successfuly.
    '''
    for (createdtime, caption, picname, pichost) in picname_list:

        download_url = '%s/large/%s' % (pichost, picname)
        if len(caption) > 168:
            caption = caption[0:167] + u'…'
        filename = createdtime + '.' + caption +'.' + picname
        filename = clean_filename(filename)

        try:
            urllib.urlretrieve(download_url, './' + sort_dir + '/' + filename)
        except Exception,e:
            print e
            sys.stderr.write('%s has download failed, add to retry queue!' % picname)
            continue
        mylock.acquire()
        retry_list.remove((createdtime, caption, picname, pichost))
        mylock.release()
        print 'Download ' + picname + ' successful.'


def get_idlist(sort_dir):
    ''' get the pic_id which has already downloaded.
    '''
    filename = os.path.join(sort_dir, 'id_list.log')
    if os.path.exists(filename):
        f = open(filename, 'r')
        id_list = f.readlines()
        f.close()
    else:
        id_list = []

    return id_list


def set_idlist(sort_dir, ids):
    ''' store the pic_id into id_list.log
    '''
    filename = os.path.join(sort_dir, 'id_list.log')
    f = open(filename, 'a')
    ids = [picname + '\n' for (createdtime, caption, picname, pichost) in ids]

    f.writelines(ids)
    f.close()


def get_album_id(UID):
    url = 'http://photo.weibo.com/albums/get_all?uid=%s&page=1&count=20' % UID
    rep = session.get(url)
    ALBUM_ID = rep.json()['data']['album_list'][0]['album_id']


def clean_filename(s, minimal_change=True):
    """
    Sanitize a string to be used as a filename.

    If minimal_change is set to true, then we only strip the bare minimum of
    characters that are problematic for filesystems (namely, ':', '/' and
    '\x00', '\n').
    """

    # strip paren portions which contain trailing time length (...)
    s = s.replace(':', '_').replace('/', '_').replace('\x00', '_').replace('\n', '').replace('\\','').replace('*','').replace('>','').replace('<','').replace('?','').replace('\"','').replace('|','')

    if minimal_change:
        return s

    s = re.sub(r"\([^\(]*$", '', s)
    s = s.replace('&nbsp;', '')
    s = s.replace('?','')
    s = s.replace('"','\'')
    s = s.strip().replace(' ', '_')

    return s

if __name__ == '__main__':
    from pprint import pprint

    pprint(wblogin(USER_ID, USER_PWD))
