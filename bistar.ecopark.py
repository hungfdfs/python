import time
import json
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException, 
    TimeoutException,
    NoSuchElementException
)

class FacebookCommentCrawler:
    def __init__(self, chrome_driver_path, cookies_file, page_url):
        self.chrome_driver_path = chrome_driver_path
        self.cookies_file = cookies_file
        self.page_url = page_url
        self.driver = None
        self.comments_data = []
        
        # Lấy handle fanpage (phần sau facebook.com/)
        self.page_handle = self.page_url.strip("/").split("facebook.com/")[-1].split("/")[0]
        
        # Set để tránh trùng dữ liệu: so sánh theo (post_url, comment_text)
        self.seen_comments = set()

    def setup_driver(self):
        print("[INFO] Setting up WebDriver...")
        chrome_options = Options()
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--start-maximized")
        # Bỏ dòng cảnh báo automation
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        
        service = Service(self.chrome_driver_path)
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

    def load_cookies(self):
        print("[INFO] Loading cookies...")
        self.driver.get("https://www.facebook.com/")
        time.sleep(5)
        with open(self.cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
            for cookie in cookies:
                if 'sameSite' in cookie:
                    del cookie['sameSite']
                self.driver.add_cookie(cookie)
        self.driver.refresh()
        time.sleep(5)
        print("[INFO] Cookies loaded and page refreshed.")

    def get_post_links(self):
        """
        Lấy link bài viết từ tab posts của fanpage.
        Chỉ lưu lại các link có định dạng chứa "/{page_handle}/posts/" hoặc "/{page_handle}/permalink/".
        Cuộn trang liên tục cho đến khi không có bài viết mới được tải.
        """
        print("[INFO] Fetching post links...")
        self.driver.get(f"https://www.facebook.com/{self.page_handle}/posts/")
        time.sleep(5)
        
        post_links = set()
        previous_count = 0
        attempts = 0
        max_attempts = 10  # số lần thử cuộn nếu không có link mới

        while attempts < max_attempts:
            posts = self.driver.find_elements(
                By.XPATH, 
                f"//a[contains(@href, '/story.php?') or contains(@href, '/{self.page_handle}/posts/') or contains(@href, '/{self.page_handle}/permalink/')]"
            )
            for post in posts:
                try:
                    link = post.get_attribute("href")
                    if link and ("/story.php?" in link 
                                 or f"/{self.page_handle}/posts/" in link 
                                 or f"/{self.page_handle}/permalink/" in link):
                        post_links.add(link)
                except StaleElementReferenceException:
                    continue
            print(f"[INFO] Collected {len(post_links)} links so far.")
            # Nếu có bài viết mới được tải, reset attempts
            if len(post_links) > previous_count:
                previous_count = len(post_links)
                attempts = 0
            else:
                attempts += 1
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(5)
        print(f"[INFO] Found {len(post_links)} valid post links.")
        return list(post_links)

    def select_all_comments(self):
        """
        Chuyển sang chế độ 'Tất cả bình luận' nếu có.
        """
        print("[INFO] Selecting 'All comments' filter if available...")
        try:
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, 
                    "//div[contains(@aria-label,'Bộ lọc bình luận') or contains(@aria-label,'Comment filter')]"))
            ).click()
            
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH,
                    "//span[contains(text(),'Tất cả bình luận') or contains(text(),'All comments')]"))
            ).click()
            time.sleep(2)
        except Exception as e:
            print(f"[ERROR] Could not select all comments: {str(e)}")

    def expand_comments(self):
        """
        Click nút "Xem thêm bình luận" để mở rộng danh sách bình luận.
        """
        print("[INFO] Expanding comments...")
        max_attempts = 10
        while max_attempts > 0:
            try:
                more_buttons = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_all_elements_located((By.XPATH, 
                        "//div[contains(text(), 'Xem thêm bình luận') or contains(text(), 'View more comments')]"))
                )
                for btn in more_buttons:
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
                max_attempts -= 1
            except TimeoutException:
                break
            except Exception:
                break

    def get_post_content(self):
        """
        Thử lấy nội dung bài viết (post_content) trong pop-up (dialog) trước,
        nếu không có thì fallback lấy theo kiểu cũ (trong role='article').
        """
        # 1) Thử lấy nội dung trong pop-up
        try:
            dialog_post_elem = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.XPATH, 
                    "//div[@role='dialog']//div[@data-ad-preview='message']"))
            )
            return dialog_post_elem.text.strip()
        except:
            pass
        
        # 2) Fallback: Lấy trong role='article'
        try:
            article_post_elem = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.XPATH, 
                    "//div[@role='article']//div[@dir='auto']"))
            )
            return article_post_elem.text.strip()
        except:
            return "Không thể trích xuất nội dung bài đăng"

    def get_comment_elements(self):
        """
        Tìm danh sách bình luận trước tiên trong pop-up (dialog), 
        nếu không thấy thì fallback tìm bình luận toàn trang.
        """
        # 1) Thử tìm bình luận trong pop-up (dialog)
        comment_elements = self.driver.find_elements(
            By.XPATH, 
            "//div[@role='dialog']//div[contains(@aria-label,'Bình luận') or contains(@aria-label,'comment')]"
        )
        if comment_elements:
            return comment_elements
        
        # 2) Fallback: Tìm bình luận ngoài pop-up
        comment_elements = self.driver.find_elements(
            By.XPATH, 
            "//div[contains(@aria-label,'Bình luận') or contains(@aria-label,'comment')]"
        )
        return comment_elements

    def get_comments_from_post(self, post_url):
        """
        Mở bài viết bằng link trực tiếp và lấy:
        - post_content: nội dung bài đăng
        - comment_text: nội dung bình luận
        """
        print(f"[INFO] Processing post: {post_url}")
        try:
            self.driver.get(post_url)
            time.sleep(5)
            
            # Lấy nội dung bài đăng (pop-up trước, fallback sau)
            post_content = self.get_post_content()
            
            # Chuyển sang chế độ "Tất cả bình luận" (nếu có) và mở rộng bình luận
            self.select_all_comments()
            time.sleep(2)
            self.expand_comments()
            time.sleep(2)
            
            # Lấy danh sách bình luận
            comment_elements = self.get_comment_elements()
            print(f"[INFO] Found {len(comment_elements)} comments in the post.")
            
            i = 0
            max_retries = 5
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    # Mỗi vòng lặp, re-fetch comment_elements để tránh stale
                    comment_elements = self.get_comment_elements()
                    if i >= len(comment_elements):
                        break
                    print(f"[INFO] Processing comment {i+1}/{len(comment_elements)}")
                    comment = comment_elements[i]
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", comment)
                    time.sleep(0.5)
                    
                    try:
                        comment_text_elem = comment.find_element(By.XPATH, ".//div[@dir='auto']")
                        comment_text = comment_text_elem.text.strip()
                    except NoSuchElementException:
                        comment_text = comment.text.strip()
                    except Exception:
                        comment_text = comment.text.strip()
                    
                    if comment_text and (post_url, comment_text) not in self.seen_comments:
                        self.seen_comments.add((post_url, comment_text))
                        self.comments_data.append({
                            'post_url': post_url,
                            'post_content': post_content,
                            'comment_text': comment_text
                        })
                        print(f"[INFO] Fetched comment: {comment_text[:30]}...")
                    i += 1
                    retry_count = 0
                except StaleElementReferenceException:
                    retry_count += 1
                    print(f"[WARN] StaleElementReferenceException encountered. Retry {retry_count}/{max_retries}")
                    time.sleep(2)
                except Exception as ex:
                    print(f"[ERROR] Error processing a comment: {ex}")
                    i += 1
                    retry_count = 0
                    time.sleep(1)
        except Exception as e:
            print(f"[ERROR] Error processing post {post_url}: {str(e)}")

    def save_to_excel(self, filename="facebook_comments.xlsx"):
        df = pd.DataFrame(self.comments_data)
        df.to_excel(filename, index=False)
        print(f"[INFO] Data saved to {filename}")

    def crawl_fanpage(self):
        try:
            print("[INFO] Starting Facebook comment crawler...")
            self.setup_driver()
            self.load_cookies()
            post_urls = self.get_post_links()
            print("[INFO] Starting crawl for all posts...")
            for post_url in post_urls:
                self.get_comments_from_post(post_url)
                time.sleep(2)
            self.save_to_excel()
        finally:
            if self.driver:
                self.driver.quit()
                print("[INFO] WebDriver closed.")

if __name__ == "__main__":
    # Điều chỉnh đường dẫn chromedriver, file cookies, và URL fanpage cho phù hợp
    chrome_driver_path = "F:/chromedriver-win64/chromedriver.exe"
    cookies_file = "cookies.json"
    page_url = "https://www.facebook.com/bistar.ecopark"
    
    crawler = FacebookCommentCrawler(chrome_driver_path, cookies_file, page_url)
    crawler.crawl_fanpage()
