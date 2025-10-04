import base64
import random
import time
import traceback
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode, quote

import requests
from bs4 import BeautifulSoup

from common.Logger import logger
from common.config import Config
from common.translations import get_translator

# 获取翻译函数
t = get_translator().t


class GitHubClient:
    GITHUB_API_URL = "https://api.github.com/search/code"
    GITHUB_WEB_SEARCH_URL = "https://github.com/search"

    def __init__(self, tokens: List[str], auth_mode: str = 'token', github_sessions: List[str] = None):
        """
        初始化GitHub客户端
        
        Args:
            tokens: GitHub Token列表（token模式使用）
            auth_mode: 认证模式，'token' 或 'web'
            github_sessions: GitHub的user_session cookie列表（web模式使用，支持多个轮询）
        """
        self.auth_mode = auth_mode.lower()
        self.tokens = [token.strip() for token in tokens if token.strip()]
        self._token_ptr = 0
        
        # 处理sessions参数（兼容旧的字符串参数和新的列表参数）
        if github_sessions is None:
            github_sessions = []
        elif isinstance(github_sessions, str):
            # 兼容旧的字符串参数
            github_sessions = [github_sessions.strip()] if github_sessions.strip() else []
        else:
            github_sessions = [s.strip() for s in github_sessions if s.strip()]
        
        self.github_sessions = github_sessions
        self._session_ptr = 0
        
        # Web模式：创建session对象
        if self.auth_mode == 'web':
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            })
            
            if self.github_sessions:
                logger.info(f"🌐 GitHub客户端初始化完成（Web模式，{len(self.github_sessions)} 个session cookie）")
            else:
                logger.warning("⚠️ Web模式未提供user_session cookie，可能会受到更多限制")
        else:
            self.session = None
            logger.info(f"🔑 GitHub客户端初始化完成（Token模式，{len(self.tokens)} 个token）")

    def _next_token(self) -> Optional[str]:
        """获取下一个token（轮询）"""
        if not self.tokens:
            return None

        token = self.tokens[self._token_ptr % len(self.tokens)]
        self._token_ptr += 1

        return token.strip() if isinstance(token, str) else token
    
    def _next_session(self) -> Optional[str]:
        """获取下一个session cookie（轮询）"""
        if not self.github_sessions:
            return None
        
        session_cookie = self.github_sessions[self._session_ptr % len(self.github_sessions)]
        self._session_ptr += 1
        
        return session_cookie.strip() if isinstance(session_cookie, str) else session_cookie
    
    def _set_session_cookie(self, session_cookie: str = None):
        """设置当前session的cookie"""
        if not self.session:
            return
        
        # 清除现有的 user_session cookie
        self.session.cookies.set('user_session', '', domain='.github.com', path='/')
        
        # 设置新的 cookie
        if session_cookie:
            self.session.cookies.set('user_session', session_cookie, domain='.github.com', path='/')

    def search_for_keys(self, query: str, max_retries: int = 8) -> Dict[str, Any]:
        """
        搜索密钥，根据auth_mode选择API或Web搜索
        
        Args:
            query: 搜索查询
            max_retries: 最大重试次数
            
        Returns:
            包含搜索结果的字典
        """
        if self.auth_mode == 'web':
            return self._search_web(query, max_retries)
        else:
            return self._search_api(query, max_retries)
    
    def _search_api(self, query: str, max_retries: int = 8) -> Dict[str, Any]:
        """使用GitHub API进行搜索（需要token）"""
        all_items = []
        total_count = 0
        expected_total = None
        pages_processed = 0

        # 统计信息
        total_requests = 0
        failed_requests = 0
        rate_limit_hits = 0
        failed_pages = []  # 记录失败的页码

        for page in range(1, 11):
            page_result = None
            page_success = False

            for attempt in range(1, max_retries + 1):
                current_token = self._next_token()

                headers = {
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
                }

                if current_token:
                    current_token = current_token.strip()
                    headers["Authorization"] = f"token {current_token}"

                params = {
                    "q": query,
                    "per_page": 100,
                    "page": page
                }

                try:
                    total_requests += 1
                    # 获取随机proxy配置
                    proxies = Config.get_random_proxy()
                    
                    if proxies:
                        response = requests.get(self.GITHUB_API_URL, headers=headers, params=params, timeout=30, proxies=proxies)
                    else:
                        response = requests.get(self.GITHUB_API_URL, headers=headers, params=params, timeout=30)
                    rate_limit_remaining = response.headers.get('X-RateLimit-Remaining')
                    # 只在剩余次数很少时警告
                    if rate_limit_remaining and int(rate_limit_remaining) < 3:
                        logger.warning(t('rate_limit_low', rate_limit_remaining, current_token))
                    response.raise_for_status()
                    page_result = response.json()
                    
                    page_success = True
                    break

                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response else None
                    failed_requests += 1
                    
                    # 获取token显示（脱敏处理）
                    token_display = current_token[:20] if current_token else "None"
                    
                    # 尝试从响应中提取详细错误信息
                    error_message = "Unknown error"
                    try:
                        if e.response is not None:
                            error_json = e.response.json()
                            error_message = error_json.get('message', str(e))
                        else:
                            error_message = str(e)
                    except:
                        error_message = str(e)
                    
                    # 根据不同的状态码提供详细的错误信息
                    if status == 401:
                        # Token 无效
                        logger.error(t('token_invalid', token_display, error_message))
                        time.sleep(2 ** attempt)
                        continue
                    elif status == 403:
                        # Token 被禁止或权限不足，可能是速率限制
                        rate_limit_hits += 1
                        rate_limit_remaining = e.response.headers.get('X-RateLimit-Remaining', 'N/A')
                        rate_limit_reset = e.response.headers.get('X-RateLimit-Reset', 'N/A')
                        
                        # 转换重置时间为可读格式
                        if rate_limit_reset != 'N/A':
                            try:
                                from datetime import datetime
                                reset_time = datetime.fromtimestamp(int(rate_limit_reset)).strftime('%Y-%m-%d %H:%M:%S')
                            except:
                                reset_time = rate_limit_reset
                        else:
                            reset_time = 'N/A'
                        
                        # 判断是否是速率限制
                        if 'rate limit' in error_message.lower() or rate_limit_remaining == '0':
                            logger.warning(t('token_rate_limited', token_display, rate_limit_remaining, reset_time))
                        else:
                            logger.error(t('token_forbidden', token_display, error_message))
                        
                        wait = min(2 ** attempt + random.uniform(0, 1), 60)
                        if attempt >= 3:
                            logger.warning(t('rate_limit_hit', status, attempt, max_retries, wait))
                        time.sleep(wait)
                        continue
                    elif status == 422:
                        # 查询语法错误（Unprocessable Entity）
                        logger.error(t('query_syntax_error', query[:80], error_message))
                        # 查询语法错误不需要重试，返回特殊标记
                        return {"items": [], "total_count": 0, "query_syntax_error": True}
                    elif status == 429:
                        # 明确的速率限制
                        rate_limit_hits += 1
                        rate_limit_remaining = e.response.headers.get('X-RateLimit-Remaining', '0')
                        rate_limit_reset = e.response.headers.get('X-RateLimit-Reset', 'N/A')
                        
                        # 转换重置时间
                        if rate_limit_reset != 'N/A':
                            try:
                                from datetime import datetime
                                reset_time = datetime.fromtimestamp(int(rate_limit_reset)).strftime('%Y-%m-%d %H:%M:%S')
                            except:
                                reset_time = rate_limit_reset
                        else:
                            reset_time = 'N/A'
                        
                        logger.warning(t('token_rate_limited', token_display, rate_limit_remaining, reset_time))
                        wait = min(2 ** attempt + random.uniform(0, 1), 60)
                        time.sleep(wait)
                        continue
                    else:
                        # 其他HTTP错误
                        if attempt == max_retries:
                            logger.error(t('token_error_detail', status or 'None', token_display, error_message))
                        time.sleep(2 ** attempt)
                        continue

                except requests.exceptions.RequestException as e:
                    failed_requests += 1
                    wait = min(2 ** attempt, 30)

                    # 只在最后一次尝试时记录网络错误
                    if attempt == max_retries:
                        logger.error(t('network_error', max_retries, page, type(e).__name__))

                    time.sleep(wait)
                    continue

            if not page_success or not page_result:
                if page == 1:
                    # 第一页失败是严重问题
                    logger.error(t('first_page_failed', query[:50]))
                    break
                # 记录失败页面信息，便于诊断
                failed_pages.append(page)
                logger.warning(f"⚠️ 第 {page} 页请求失败，已跳过（可能导致数据丢失）")
                continue

            pages_processed += 1

            if page == 1:
                total_count = page_result.get("total_count", 0)
                expected_total = min(total_count, 1000)
                
                if total_count > 0:
                    logger.info(f"   🔢 GitHub返回总数: {total_count} (预期获取: {expected_total})")

            items = page_result.get("items", [])
            current_page_count = len(items)

            if current_page_count == 0:
                if expected_total and len(all_items) < expected_total:
                    continue
                else:
                    break

            all_items.extend(items)

            if expected_total and len(all_items) >= expected_total:
                break

            if page < 10:
                sleep_time = random.uniform(0.5, 1.5)
                logger.info(t('processing_query', query, page, current_page_count, expected_total, total_count, sleep_time))
                time.sleep(sleep_time)

        final_count = len(all_items)

        # 检查数据完整性
        if expected_total and final_count < expected_total:
            discrepancy = expected_total - final_count
            if discrepancy > expected_total * 0.1:  # 超过10%数据丢失
                warning_msg = t('data_loss_warning', discrepancy, expected_total, discrepancy / expected_total * 100)
                if failed_pages:
                    warning_msg += f" | 失败页面: {failed_pages}"
                logger.warning(warning_msg)

        # 主要成功日志 - 一条日志包含所有关键信息
        logger.info(t('search_complete', query, pages_processed, final_count, expected_total or '?', total_requests))

        result = {
            "total_count": total_count,
            "incomplete_results": final_count < expected_total if expected_total else False,
            "items": all_items
        }

        return result
    
    def _search_web(self, query: str, max_retries: int = 8) -> Dict[str, Any]:
        """使用Web方式搜索（基于cookie认证）"""
        all_items = []
        total_count = 0
        expected_total = None
        pages_processed = 0
        
        # 统计信息
        total_requests = 0
        failed_requests = 0
        failed_pages = []
        
        logger.info(f"🌐 使用Web模式搜索: {query[:50]}...")
        
        for page in range(1, 11):
            page_success = False
            
            # 每个新页面轮换一次session cookie（如果有多个）
            if self.github_sessions and len(self.github_sessions) > 1:
                current_session = self._next_session()
                self._set_session_cookie(current_session)
            
            for attempt in range(1, max_retries + 1):
                try:
                    total_requests += 1
                    
                    # 构建搜索URL
                    params = {
                        'q': query,
                        'type': 'code',
                        'p': page
                    }
                    
                    # 获取随机proxy配置
                    proxies = Config.get_random_proxy()
                    
                    # 发送请求
                    if proxies:
                        response = self.session.get(
                            self.GITHUB_WEB_SEARCH_URL,
                            params=params,
                            timeout=30,
                            proxies=proxies,
                            allow_redirects=True
                        )
                    else:
                        response = self.session.get(
                            self.GITHUB_WEB_SEARCH_URL,
                            params=params,
                            timeout=30,
                            allow_redirects=True
                        )
                    
                    response.raise_for_status()
                    
                    # 解析HTML
                    items = self._parse_search_results(response.text)
                    
                    if page == 1:
                        # 从第一页估算总数
                        total_count = self._estimate_total_count(response.text)
                        expected_total = min(total_count, 1000)
                        
                        if total_count > 0:
                            logger.info(f"   🔢 Web搜索估算总数: {total_count} (预期获取: {expected_total})")
                    
                    current_page_count = len(items)
                    
                    if current_page_count == 0:
                        if expected_total and len(all_items) < expected_total:
                            # 可能被GitHub限制了，尝试延长等待
                            wait_time = min(2 ** attempt, 30)
                            logger.warning(f"⚠️ 第 {page} 页返回0结果，等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            page_success = True
                            break
                    
                    all_items.extend(items)
                    pages_processed += 1
                    page_success = True
                    
                    break
                    
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response else None
                    failed_requests += 1
                    
                    if status == 429:
                        # 速率限制
                        wait = min(2 ** attempt + random.uniform(0, 1), 60)
                        logger.warning(f"⏰ Web模式遭遇速率限制 (429)，等待 {wait:.1f} 秒...")
                        time.sleep(wait)
                        continue
                    elif status == 422:
                        # 查询语法错误
                        logger.error(f"❌ 查询语法错误: {query[:80]}")
                        return {"items": [], "total_count": 0, "query_syntax_error": True}
                    else:
                        if attempt == max_retries:
                            logger.error(f"❌ HTTP错误 {status}: {str(e)}")
                        time.sleep(2 ** attempt)
                        continue
                
                except requests.exceptions.RequestException as e:
                    failed_requests += 1
                    wait = min(2 ** attempt, 30)
                    
                    if attempt == max_retries:
                        logger.error(f"❌ 网络错误（第{page}页）: {type(e).__name__}")
                    
                    time.sleep(wait)
                    continue
            
            if not page_success:
                if page == 1:
                    logger.error(f"❌ 第一页请求失败: {query[:50]}")
                    break
                failed_pages.append(page)
                logger.warning(f"⚠️ 第 {page} 页请求失败，已跳过")
                continue
            
            if expected_total and len(all_items) >= expected_total:
                break
            
            # 没有更多结果
            if len(items) == 0:
                break
            
            # 页面间延迟
            if page < 10:
                sleep_time = random.uniform(2, 4)  # Web模式延迟更长
                logger.info(f"   📄 第{page}页: {len(items)}个结果 | 预期总数: {expected_total or '?'} | 等待 {sleep_time:.1f}s")
                time.sleep(sleep_time)
        
        final_count = len(all_items)
        
        # 检查数据完整性
        if expected_total and final_count < expected_total:
            discrepancy = expected_total - final_count
            if discrepancy > expected_total * 0.1:
                warning_msg = f"⚠️ 数据可能不完整: 缺少 {discrepancy}/{expected_total} ({discrepancy / expected_total * 100:.1f}%)"
                if failed_pages:
                    warning_msg += f" | 失败页面: {failed_pages}"
                logger.warning(warning_msg)
        
        logger.info(f"✅ Web搜索完成: {query[:50]}... | 页数: {pages_processed} | 结果: {final_count}/{expected_total or '?'} | 请求: {total_requests}")
        
        result = {
            "total_count": total_count,
            "incomplete_results": final_count < expected_total if expected_total else False,
            "items": all_items
        }
        
        return result
    
    def _parse_search_results(self, html: str) -> List[Dict[str, Any]]:
        """解析GitHub搜索结果HTML页面"""
        items = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 方法1: 通过search-title找到结果容器
            # GitHub现在使用这种结构
            result_containers = soup.find_all('div', class_=lambda x: x and 'search-title' in x if x else False)
            
            if not result_containers:
                # 备用方法：直接查找所有blob链接
                logger.warning("⚠️ 未找到结果容器，尝试备用方法查找blob链接")
                blob_links = soup.find_all('a', href=lambda x: x and '/blob/' in x if x else False)
                
                # 过滤掉重复的链接（同一个文件可能有多个行号链接）
                seen_files = set()
                for link in blob_links:
                    href = link.get('href', '')
                    # 移除行号锚点
                    file_url = href.split('#')[0]
                    if file_url not in seen_files:
                        seen_files.add(file_url)
                        result_containers.append(link.parent.parent if link.parent and link.parent.parent else link.parent)
            
            # 用于去重
            seen_files = set()
            
            for container in result_containers:
                if not container:
                    continue
                    
                try:
                    # 在容器中查找blob链接
                    link_elem = container.find('a', href=lambda x: x and '/blob/' in x if x else False)
                    
                    if not link_elem:
                        continue
                    
                    file_url = link_elem.get('href', '')
                    if not file_url:
                        continue
                    
                    # 移除行号锚点（如 #L274）
                    file_url = file_url.split('#')[0]
                    
                    # 去重
                    if file_url in seen_files:
                        continue
                    seen_files.add(file_url)
                        
                    if not file_url.startswith('http'):
                        file_url = 'https://github.com' + file_url
                    
                    # 从URL解析仓库和文件路径
                    # URL格式: https://github.com/{owner}/{repo}/blob/{branch}/{path}
                    # 例如: /Benjamin-Loison/YouTube-operational-API/blob/0d2768a5fcf560288eb3a9fa573056bdd5dba3d2/index.php
                    url_parts = file_url.replace('https://github.com/', '').split('/')
                    
                    if len(url_parts) >= 5 and url_parts[2] == 'blob':
                        owner = url_parts[0]
                        repo = url_parts[1]
                        branch = url_parts[3]
                        # blob和branch之后是文件路径
                        file_path = '/'.join(url_parts[4:])
                        
                        # 构造item对象（模拟API返回格式）
                        item = {
                            'name': url_parts[-1] if url_parts else '',
                            'path': file_path,
                            'sha': f"web_{hash(file_url) & 0xFFFFFFFF:08x}",  # 生成伪SHA
                            'url': file_url,
                            'html_url': file_url,
                            'repository': {
                                'full_name': f"{owner}/{repo}",
                                'name': repo,
                                'owner': {'login': owner},
                                'pushed_at': None  # Web搜索无法获取这个信息
                            }
                        }
                        
                        items.append(item)
                
                except Exception as e:
                    logger.debug(f"解析单个搜索结果时出错: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"解析搜索结果HTML失败: {e}")
            logger.error(traceback.format_exc())
        
        logger.info(f"📋 成功解析 {len(items)} 个搜索结果")
        return items
    
    def _estimate_total_count(self, html: str) -> int:
        """从HTML中估算搜索结果总数"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找显示结果总数的元素
            # GitHub通常在页面顶部显示类似 "123,456 results" 的文本
            count_elem = soup.find('h3', string=lambda text: text and 'result' in text.lower())
            
            if count_elem:
                text = count_elem.get_text()
                # 提取数字
                import re
                numbers = re.findall(r'[\d,]+', text)
                if numbers:
                    count_str = numbers[0].replace(',', '')
                    return int(count_str)
            
            # 如果找不到精确数字，根据结果数量估算
            result_count = len(soup.find_all('div', {'class': 'code-list-item'}))
            if result_count > 0:
                return result_count * 10  # 粗略估算
        
        except Exception as e:
            logger.debug(f"估算结果总数失败: {e}")
        
        return 100  # 默认值

    def get_file_content(self, item: Dict[str, Any]) -> Optional[str]:
        """
        获取文件内容，根据auth_mode选择API或Web方式
        
        Args:
            item: 文件信息字典
            
        Returns:
            文件内容字符串，失败返回None
        """
        if self.auth_mode == 'web':
            return self._get_file_content_web(item)
        else:
            return self._get_file_content_api(item)
    
    def _get_file_content_api(self, item: Dict[str, Any]) -> Optional[str]:
        """使用API获取文件内容（需要token）"""
        repo_full_name = item["repository"]["full_name"]
        file_path = item["path"]

        metadata_url = f"https://api.github.com/repos/{repo_full_name}/contents/{file_path}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
        }

        token = self._next_token()
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            # 获取proxy配置
            proxies = Config.get_random_proxy()

            logger.info(t('processing_file', metadata_url))
            if proxies:
                metadata_response = requests.get(metadata_url, headers=headers, proxies=proxies)
            else:
                metadata_response = requests.get(metadata_url, headers=headers)

            metadata_response.raise_for_status()
            file_metadata = metadata_response.json()

            # 检查返回的是否为列表（目录内容）而非单个文件
            if isinstance(file_metadata, list):
                logger.warning(t('unexpected_list_response', metadata_url))
                return None

            # 检查是否有base64编码的内容
            encoding = file_metadata.get("encoding")
            content = file_metadata.get("content")
            
            if encoding == "base64" and content:
                try:
                    # 解码base64内容
                    decoded_content = base64.b64decode(content).decode('utf-8')
                    return decoded_content
                except Exception as e:
                    logger.warning(t('decode_failed', e))
            
            # 如果没有base64内容或解码失败，使用原有的download_url逻辑
            download_url = file_metadata.get("download_url")
            if not download_url:
                logger.warning(t('no_download_url', metadata_url))
                return None

            if proxies:
                content_response = requests.get(download_url, headers=headers, proxies=proxies)
            else:
                content_response = requests.get(download_url, headers=headers)
            logger.info(t('checking_keys_from', download_url, content_response.status_code))
            content_response.raise_for_status()
            return content_response.text

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else None
            token_display = token[:20] if token else "None"
            
            # 尝试从响应中提取详细错误信息
            error_message = "Unknown error"
            try:
                if e.response is not None:
                    error_json = e.response.json()
                    error_message = error_json.get('message', str(e))
                else:
                    error_message = str(e)
            except:
                error_message = str(e)
            
            # 根据不同的状态码提供详细的错误信息
            if status == 401:
                logger.error(t('token_invalid', token_display, error_message))
            elif status == 403:
                rate_limit_remaining = e.response.headers.get('X-RateLimit-Remaining', 'N/A')
                rate_limit_reset = e.response.headers.get('X-RateLimit-Reset', 'N/A')
                
                if rate_limit_reset != 'N/A':
                    try:
                        from datetime import datetime
                        reset_time = datetime.fromtimestamp(int(rate_limit_reset)).strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        reset_time = rate_limit_reset
                else:
                    reset_time = 'N/A'
                
                if 'rate limit' in error_message.lower() or rate_limit_remaining == '0':
                    logger.warning(t('token_rate_limited', token_display, rate_limit_remaining, reset_time))
                else:
                    logger.error(t('token_forbidden', token_display, error_message))
            elif status == 429:
                rate_limit_remaining = e.response.headers.get('X-RateLimit-Remaining', '0')
                rate_limit_reset = e.response.headers.get('X-RateLimit-Reset', 'N/A')
                
                if rate_limit_reset != 'N/A':
                    try:
                        from datetime import datetime
                        reset_time = datetime.fromtimestamp(int(rate_limit_reset)).strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        reset_time = rate_limit_reset
                else:
                    reset_time = 'N/A'
                
                logger.warning(t('token_rate_limited', token_display, rate_limit_remaining, reset_time))
            else:
                logger.error(t('token_error_detail', status or 'None', token_display, error_message))
            
            return None
        except requests.exceptions.RequestException as e:
            logger.error(t('fetch_file_failed', metadata_url, type(e).__name__))
            return None
    
    def _get_file_content_web(self, item: Dict[str, Any]) -> Optional[str]:
        """使用Web方式直接获取raw文件内容（基于cookie认证）"""
        try:
            # 轮换session cookie（如果有多个）
            if self.github_sessions and len(self.github_sessions) > 1:
                current_session = self._next_session()
                self._set_session_cookie(current_session)
            
            repo_full_name = item["repository"]["full_name"]
            file_path = item["path"]
            
            # 构建raw URL
            # 格式: https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
            # 由于从web搜索无法直接获取branch，我们尝试常见的分支名
            branches_to_try = ['main', 'master', 'develop']
            
            # 如果html_url中包含blob信息，尝试从中提取branch
            html_url = item.get('html_url', '')
            if '/blob/' in html_url:
                parts = html_url.split('/blob/')
                if len(parts) > 1:
                    branch_and_path = parts[1].split('/', 1)
                    if branch_and_path:
                        branches_to_try.insert(0, branch_and_path[0])
            
            proxies = Config.get_random_proxy()
            
            for branch in branches_to_try:
                raw_url = f"https://raw.githubusercontent.com/{repo_full_name}/{branch}/{file_path}"
                
                try:
                    logger.info(f"🌐 获取文件内容: {raw_url}")
                    
                    if proxies:
                        response = self.session.get(raw_url, timeout=30, proxies=proxies)
                    else:
                        response = self.session.get(raw_url, timeout=30)
                    
                    if response.status_code == 200:
                        logger.info(f"✅ 成功获取文件内容 (branch={branch})")
                        return response.text
                    elif response.status_code == 404:
                        # 分支不存在，尝试下一个
                        continue
                    else:
                        response.raise_for_status()
                
                except requests.exceptions.RequestException:
                    # 尝试下一个分支
                    continue
            
            logger.warning(f"⚠️ 无法获取文件内容: {repo_full_name}/{file_path} (尝试了分支: {branches_to_try})")
            return None
        
        except Exception as e:
            logger.error(f"❌ 获取文件内容失败: {e}")
            return None

    @staticmethod
    def create_instance(tokens: List[str], auth_mode: str = None, github_sessions: List[str] = None) -> 'GitHubClient':
        """
        创建GitHubClient实例
        
        Args:
            tokens: GitHub Token列表
            auth_mode: 认证模式，如果为None则从Config读取
            github_sessions: GitHub session cookie列表，如果为None则从Config读取
            
        Returns:
            GitHubClient实例
        """
        if auth_mode is None:
            auth_mode = Config.GITHUB_AUTH_MODE
        if github_sessions is None:
            github_sessions = Config.GITHUB_SESSIONS
        return GitHubClient(tokens, auth_mode, github_sessions)
