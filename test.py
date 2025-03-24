import time
import json
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

class FacebookCommentCrawler:
    def __init__(self, chrome_driver_path, cookies_file, page_url):
        self.chrome_driver_path = chrome_driver_path
        self.cookies_file = cookies_file
        self.page_url = page_url
        self.driver = None
        self.comments_data = []
        
        # Lấy handle fanpage (phần sau facebook.com/)
        self.page_handle = self.page_url.strip("/").split("facebook.com/")[-1].split("/")[0]
        
        # Set để tránh trùng bình luận
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
        time.sleep(3)
        with open(self.cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
            for cookie in cookies:
                # Xử lý trường sameSite nếu có
                if 'sameSite' in cookie:
                    del cookie['sameSite']
                self.driver.add_cookie(cookie)
        self.driver.refresh()
        time.sleep(5)
        print("[INFO] Cookies loaded and page refreshed.")

    def get_post_links(self):
        """Lấy link bài viết từ tab posts của fanpage."""
        print("[INFO] Fetching post links...")
        self.driver.get(f"https://www.facebook.com/{self.page_handle}/posts/")
        time.sleep(5)
        
        post_links = set()
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        scroll_attempt = 0
        max_scroll_attempts = 5

        while scroll_attempt < max_scroll_attempts:
            # Tìm các link dẫn đến bài viết
            posts = self.driver.find_elements(
                By.XPATH, 
                f"//a[contains(@href, '/story.php?') or contains(@href, '/{self.page_handle}/posts/') or contains(@href, '/{self.page_handle}/permalink/')]"
            )
            
            for post in posts:
                try:
                    # Lấy toàn bộ link, KHÔNG split('?')[0] để giữ query string
                    link = post.get_attribute("href")
                    if link:
                        # Điều kiện kiểm tra link hợp lệ (có thể tuỳ chỉnh thêm)
                        if ("/story.php?" in link 
                            or f"/{self.page_handle}/posts/" in link 
                            or f"/{self.page_handle}/permalink/" in link):
                            post_links.add(link)
                except StaleElementReferenceException:
                    continue

            # Cuộn trang xuống cuối
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            
            if new_height == last_height:
                scroll_attempt += 1
                time.sleep(2)
            else:
                scroll_attempt = 0
                last_height = new_height

        print(f"[INFO] Found {len(post_links)} valid post links.")
        return list(post_links)

    def select_all_comments(self):
        """Chuyển sang chế độ xem tất cả bình luận (nếu có)."""
        print("[INFO] Selecting 'All comments' filter if available...")
        try:
            # Mở menu filter (Bộ lọc bình luận)
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH, 
                    "//div[contains(@aria-label,'Bộ lọc bình luận') or contains(@aria-label,'Comment filter')]"
                ))
            ).click()
            
            # Chọn chế độ tất cả bình luận
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//span[contains(.,'Tất cả bình luận') or contains(.,'All comments')]"
                ))
            ).click()
            time.sleep(2)
        except Exception as e:
            print(f"[ERROR] Could not select all comments: {str(e)}")

    def expand_comments(self):
        """Mở rộng tất cả bình luận (nút Xem thêm bình luận)."""
        print("[INFO] Expanding comments...")
        max_attempts = 10
        while max_attempts > 0:
            try:
                more_buttons = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_all_elements_located((
                        By.XPATH, 
                        "//div[contains(text(), 'Xem thêm bình luận') or contains(text(), 'View more comments')]"
                    ))
                )
                
                # Click từng nút 'Xem thêm bình luận'
                for btn in more_buttons:
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
                max_attempts -= 1
            except (TimeoutException, StaleElementReferenceException):
                break

    def get_comments_from_post(self, post_url):
        """Mở bài viết (bằng link trực tiếp), sau đó lấy bình luận."""
        print(f"[INFO] Processing post: {post_url}")
        try:
            self.driver.get(post_url)
            time.sleep(3)
            
            # Chuyển sang chế độ xem tất cả bình luận (nếu có filter)
            self.select_all_comments()
            
            # Mở rộng bình luận nhiều tầng
            self.expand_comments()
            time.sleep(2)

            # Tìm tất cả div bình luận
            comments = WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((
                    By.XPATH, 
                    "//div[contains(@aria-label,'Bình luận') or contains(@aria-label,'Comment')]"
                ))
            )
            
            for comment in comments:
                try:
                    text_element = comment.find_element(By.XPATH, ".//div[@dir='auto']")
                    text = text_element.text.strip()
                    
                    if not text:
                        continue  # bỏ qua comment trống
                    
                    # Tạo ID để tránh trùng
                    comment_id = f"{post_url}-{text[:50]}"
                    if comment_id not in self.seen_comments:
                        self.comments_data.append({
                            'post_url': post_url,
                            'comment_text': text
                        })
                        self.seen_comments.add(comment_id)
                except Exception as e:
                    print(f"[ERROR] Error extracting comment: {str(e)}")
        except Exception as e:
            print(f"[ERROR] Error processing post {post_url}: {str(e)}")

    def save_to_excel(self, filename="facebook_comments.xlsx"):
        """Lưu kết quả ra file Excel."""
        print(f"[INFO] Saving data to {filename}...")
        pd.DataFrame(self.comments_data).to_excel(filename, index=False)
        print(f"[INFO] Saved {len(self.comments_data)} comments to {filename}")

    def crawl_fanpage(self):
        """Hàm chính thực thi quá trình crawl."""
        try:
            print("[INFO] Starting Facebook comment crawler...")
            self.setup_driver()
            self.load_cookies()
            
            # Lấy link tất cả bài viết trên fanpage
            post_urls = self.get_post_links()
            
            # Giới hạn lấy 10 bài để demo, tuỳ chỉnh theo ý bạn
            for url in post_urls[:]:
                self.get_comments_from_post(url)
                time.sleep(2)
                
            # Lưu kết quả
            self.save_to_excel()
        except Exception as e:
            print(f"[CRITICAL] A critical error occurred: {str(e)}")
        finally:
            if self.driver:
                self.driver.quit()
                print("[INFO] WebDriver closed.")

if __name__ == "__main__":
    # Cấu hình đường dẫn driver, cookies, và fanpage
    chrome_driver_path = "F:/chromedriver-win64/chromedriver.exe"
    cookies_file = "cookies.json"
    page_url = "https://www.facebook.com/bistar.ecopark"

    crawler = FacebookCommentCrawler(chrome_driver_path, cookies_file, page_url)
    crawler.crawl_fanpage()
