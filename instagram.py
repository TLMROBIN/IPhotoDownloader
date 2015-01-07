# _*_ coding=utf8 _*_

import requests
import urllib
import re
from bs4 import BeautifulSoup
import threading
import time
import os
import sys
import logging
import errno
import argparse

PHOTOS_PER_PAGE = 20
user_agent = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.71 Safari/537.36'

mylock = threading.RLock()
retry_list = []
done_list = []
web_url = "http://websta.me"
base_url = "http://websta.me/n/"

time_img_created_regex = r'data-utime=\"(?P<utime>\d+)\"'


def get_instagram_images(target_usr_list, path='.', thread_num=20):



    for (uid, uname) in target_usr_list:

        uname = clean_filename(uname)
        if len(uname) == 0:
            uname = clean_filename(str(uid))

        print '========================================================================='
        print 'Downloading %s\'s photos' % uname

        sort_dir = os.path.join(path, 'instagram.photo', uname)
        mkdir_p(sort_dir)
        id_list = get_idlist(sort_dir)
        isFirsttime = False
        if len(id_list) == 0:
            isFirsttime = True
            pass
        else:
            id_list = [ids.strip() for ids in id_list]

        url = base_url + uid
        session = requests.session()
        session.headers['User-Agent'] = user_agent
        page = get_page(session, url, retry_times=5)

        pic_list = []
        video_list = []
        print 'Parsing...'

        page_num = 1
        while page is not None:
            print 'Page %2d...' % page_num
            soup = BeautifulSoup(page)
            page = None

            for photo_item in soup.find_all('div', {'class': 'photoeach clearfix'}):

                # get caption of the img
                caption_soup = photo_item.find('div', {'class': 'caption comment_each'})
                if caption_soup:
                    caption = clean_filename(caption_soup.strong.get_text())
                else:
                    caption = ''

                img_page_url = web_url + photo_item.find('a', {'class': 'mainimg'}).get('href')

                # get real video url
                video_element = photo_item.find('a', {'class': 'fancy-video video-link  fancybox.ajax toShow hide'})
                if video_element is not None:

                    video_url = video_element.get('href')

                    # check if the video had already been downloaded
                    video_id = video_url.split('/')[-1]
                    if video_id not in id_list:
                        video_list.append((img_page_url, caption, video_id, video_url))
                        pic_list.append((img_page_url, caption, video_id, video_url))
                        logging.info('img_page_url: %s', img_page_url)
                        logging.info('%s', caption.encode('cp936', 'ignore'))
                        logging.info('video_url: %s', video_url)
                    else:
                        break
                    continue

                # get img_id
                img_real_url = photo_item.find('div', {'class': 'social_buttons pw-widget pw-size-medium pw-counter-show'}).get('pw:image')
                # check if the img had already been downloaded
                img_id = img_real_url.split('/')[-1]
                if img_id not in id_list:
                    pic_list.append((img_page_url, caption, img_id, img_real_url))
                    logging.info('img_page_url: %s', img_page_url)
                    logging.info('%s', caption.encode('cp936', 'ignore'))
                    logging.info('img_url: %s', img_real_url)
                else:
                    break

            # " len(pic_list) >= page_num * PHOTOS_PER_PAGE " means all photos in current page are brand new,
            # it's neccessary to check next page. Otherwise it's not neccessary.
            if len(pic_list) >= page_num * PHOTOS_PER_PAGE or isFirsttime:
                soup_next = soup.find('a', {'rel': 'next'})
                if soup_next:
                    link_next = soup_next.get('href')
                    if link_next:
                        logging.info("-----------------------------------------------------------------")
                        logging.info('%s', web_url + link_next)
                        page = get_page(session, web_url + link_next, retry_times=5)

            page_num += 1

        print ' '
        print '%d new items(%d videos) found since last update!' % (len(pic_list), len(video_list))

        if len(pic_list) == 0:
            continue

        # 传入整个list的大小，如果大于配置文件中的数值则按 THREAD 数目分片，否则按照 list 大小分片
        pic_list_div = div_list(pic_list, thread_num)
        threads = []
        times = len(pic_list_div)
        for i in range(times):
            th = (threading.Thread(target=download, args=(session, pic_list_div[i], sort_dir)))
            threads.append(th)
        for th in threads:
            th.start()
        for th in threads:
            th.join()
     
        
        attempts_times = 1
        while len(retry_list) != 0 and attempts_times < 5:
            print 'Now retrying download the [ %s ] failed task!!!' % len(retry_list)
            threads = []
            # 分多线程进行重试
            pic_list_div = []
            pic_list_div = div_list(retry_list, thread_num)
            for i in range(len(pic_list_div)):
                th = threading.Thread(target=retry_download, args=(session, pic_list_div[i], sort_dir))
                threads.append(th)

            for th in threads:
                th.start()
            for th in threads:
                th.join()

            attempts_times += 1

        done_list = list(set(pic_list) - set(retry_list))
        set_idlist(sort_dir, list(done_list))
        if len(retry_list)== 0:
            print 'All %s \'s download job has been done.' % uname
        else:
            print '%d items failed.' % len(retry_list)

def div_list(picname_list, thread_num):
    '''
    divide the list into small lists, if sum < thread_num then divide by sum
    '''
    sum = len(picname_list)
    if sum < thread_num:
        thread_num = sum

    # round up
    size = int((len(picname_list) + thread_num - 1) / thread_num)

    l = [picname_list[i:i + size] for i in range(0, len(picname_list), size)]

    return l


def download(session, pic_list, sort_dir):
    for (img_page_url, caption, picname, pic_url) in pic_list:
        try:
            img_page = get_page(session, img_page_url, retry_times=1)

            # get time the photo created
            utime = re.search(time_img_created_regex, img_page).group('utime')
            img_time = time.strftime('%Y%m%d%H%M', time.gmtime(float(utime)))
            logging.info('img_time: %s', img_time)

            # max length of filename including path defined by windows is 256
            # cut the caption str if needed
            cur_dir_length = len(os.path.abspath(sort_dir))
            max_cap_length = 255 - len(img_time) - len(picname) - cur_dir_length - 3
            if len(caption) > max_cap_length:
                tmp_caption = caption[0:max_cap_length] + u'…'
            else:
                tmp_caption = caption
            filename = img_time + '.' + tmp_caption + '.' + picname
            filename = clean_filename(filename)
            fn = os.path.join(sort_dir, filename)
            urllib.urlretrieve(pic_url, fn)

            print 'Download ' + picname + ' successful.'
        except Exception, e:
            print e
            mylock.acquire()
            retry_list.append((img_page_url, caption, picname, pic_url))
            mylock.release()
            print '%s  download failed, add to retry queue!' % picname


def retry_download(session, picname_list, sort_dir):
    '''
    retry to download the failed task untile all the image has been dowload successfuly.
    '''
    for (img_page_url, caption, picname, pic_url) in picname_list:

        try:
            img_page = get_page(session, img_page_url)

            # get time the photo created
            utime = re.search(time_img_created_regex, img_page).group('utime')
            img_time = time.strftime('%Y%m%d%H%M', time.gmtime(float(utime)))
            logging.info('img_time: %s', img_time)

            # max length of filename including path defined by windows is 256
            # cut the caption str if needed
            cur_dir_length = len(os.path.abspath(sort_dir))
            max_cap_length = 255 - len(img_time) - len(picname) - cur_dir_length - 3
            if len(caption) > max_cap_length:
                tmp_caption = caption[0:max_cap_length] + u'…'
            else:
                tmp_caption = caption
            filename = img_time + '.' + tmp_caption + '.' + picname
            filename = clean_filename(filename)
            fn = os.path.join(sort_dir, filename)
            urllib.urlretrieve(pic_url, fn)

            mylock.acquire()
            try:
                retry_list.remove((img_page_url, caption, picname, pic_url))
            finally:
                mylock.release()

            print 'Download ' + picname + ' successful.'
        except Exception, e:
            print e
            print '%s has download failed, add to retry queue!' % picname



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
    ids = [picname + '\n' for (img_page_url, caption, picname, pic_url) in ids]

    f.writelines(ids)
    f.close()


def clean_filename(s, minimal_change=True):
    """
    Sanitize a string to be used as a filename.

    If minimal_change is set to true, then we only strip the bare minimum of
    characters that are problematic for filesystems (namely, ':', '/' and
    '\x00', '\n').
    """

    # strip paren portions which contain trailing time length (...)
    s = s.replace(':', '_')\
        .replace('/', '_')\
        .replace('\x00', '_')\
        .replace('\n', '')\
        .replace('\\', '')\
        .replace('*', '')\
        .replace('>', '')\
        .replace('<', '')\
        .replace('?', '')\
        .replace('\"', '')\
        .replace('|', '')

    if minimal_change:
        return s

    s = re.sub(r"\([^\(]*$", '', s)
    s = s.replace('&nbsp;', '')
    s = s.replace('?', '')
    s = s.replace('"', '\'')
    s = s.strip().replace(' ', '_')

    return s


def get_page(session, url, retry_times=3, timeout=10):
    """
    Download an HTML page using the requests session.
    """
    attempts_times = 0
    while True:
        attempts_times += 1
        
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
        except Exception as e:
            if attempts_times >= retry_times:
                raise
            logging.info('Error getting page %s, retrying...')
            msg = 'Error getting page, retry in {0} seconds ...'
            interval = 2 ** attempts_times
            print(msg.format(interval))
            time.sleep(interval)
            continue
        break
    return r.text


def mkdir_p(path, mode=0o777):
    """
    Create subdirectory hierarchy given in the paths argument.
    """

    try:
        os.makedirs(path, mode)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

def parse_args():

    parser = argparse.ArgumentParser(description = 'Download photos from Instagram by user id')

    parser.add_argument('target_user_id',
                        action='store',
                        nargs='+',
                        help='ID of the one who you are interested in. (e.g. "minchen333")')

    # optional
    parser.add_argument('-n',
                        '--nickname',
                        dest='nickname',
                        action='store',
                        default='',
                        help='nickname of the id, would be name of directory')

    parser.add_argument('--path',
                        dest='path',
                        action='store',
                        default='.',
                        help='path to save the files')

    parser.add_argument('-t',
                        '--threadnum',
                        dest='threadnum',
                        action='store',
                        default='20',
                        help='the numbers of threads generated to download photos')
    
    args = parser.parse_args()
    

    return args

if __name__ == '__main__':

    args = parse_args()   

    get_instagram_images([(args.target_user_id[0], args.nickname)],
                         path=args.path,
                         thread_num=int(args.threadnum))
