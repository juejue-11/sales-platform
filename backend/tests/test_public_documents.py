import asyncio
import unittest
from unittest.mock import patch

from backend.public_documents import DocumentError, _download, discover_attachments, parse_document, validate_public_url


class PublicDocumentTests(unittest.TestCase):
    def test_onclick_attachment_is_discovered_outside_body(self):
        html = '''<div class="article-materials"><a onclick="ztbfjyz('/f/物料清单附件.pdf','0','1')">物料清单附件.pdf</a>
        <a href="https://evil.example/f.pdf">其他</a><a href="javascript:alert(1)">x</a></div>'''
        links = discover_attachments(html, "https://bid.example/notice.html")
        self.assertEqual(links, [{"url": "https://bid.example/f/物料清单附件.pdf", "name": "物料清单附件.pdf"}])

    def test_source_host_and_private_ip_rejected(self):
        with self.assertRaises(DocumentError):
            asyncio.run(validate_public_url("https://evil.example/f.pdf", "https://bid.example/n"))
        with patch("backend.public_documents.socket.getaddrinfo", return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaisesRegex(DocumentError, "non_public_source"):
                asyncio.run(validate_public_url("https://bid.example/f.pdf", "https://bid.example/n"))

    def test_unparsed_and_auth_are_explicit(self):
        doc = parse_document(b'unknown file', record_id=174, url="https://bid.example/f.xls")
        self.assertEqual(doc["status"], "unsupported")
        self.assertEqual(doc["items"], [])
        doc = parse_document(b'<html>login</html>', record_id=174, url="https://bid.example/f.pdf")
        self.assertEqual(doc["status"], "auth_required")

    def test_legacy_doc_is_automatically_converted_and_keeps_original_provenance(self):
        import hashlib
        raw = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1old doc'
        def read_pdf(content, document):
            self.assertEqual(content, b'%PDF-converted')
            document.update(status='parsed', pages=2, text='采购方案正文',
                            page_texts=[{'page': 1, 'text': '采购方案正文'}])
        with patch('backend.public_documents.convert_legacy_word', return_value=b'%PDF-converted') as convert, \
                patch('backend.public_documents._pdf', side_effect=read_pdf):
            doc = parse_document(raw, record_id=174, url='https://bid.example/f.doc',
                                 mime_type='application/msword')
        convert.assert_called_once_with(raw)
        self.assertEqual(doc['status'], 'parsed')
        self.assertEqual(doc['text'], '采购方案正文')
        self.assertEqual(doc['pages'], 2)
        self.assertEqual(doc['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(doc['mime_type'], 'application/msword')

    def test_doc_conversion_failure_has_specific_reason(self):
        from backend.legacy_word import WordConversionError
        with patch('backend.public_documents.convert_legacy_word', side_effect=WordConversionError('conversion_timeout')):
            doc = parse_document(b'old doc', record_id=174, url='https://bid.example/f.doc')
        self.assertEqual(doc['status'], 'unreadable')
        self.assertEqual(doc['error'], 'conversion_timeout')
        self.assertEqual(doc['text'], '')

    def test_fake_pdf_does_not_claim_parsed(self):
        doc = parse_document(b'%PDF-broken', record_id=174, url="https://bid.example/f.pdf")
        self.assertEqual(doc["status"], "unreadable")
        self.assertEqual(doc["text"], "")

    def test_material_table_preserves_facts_and_provenance(self):
        from types import SimpleNamespace
        header = ['序','物料名称','规格型号','材质','采购数量','计量单位','交货地/交货港','交货期']
        row = ['1','普碳\n中板','4mm','Q235B','5.0','t','本溪市','合同签订后，按通知供货量三日内供完']
        page = SimpleNamespace(extract_text=lambda:'物料清单附件', extract_tables=lambda:[[header,row]])
        class PDF:
            pages = [page]
            def __enter__(self): return self
            def __exit__(self,*args): pass
        with patch('pdfplumber.open', return_value=PDF()):
            doc = parse_document(b'%PDF-fixture', record_id=174, url='https://bid.example/f.pdf')
        item = doc['items'][0]
        self.assertEqual(item['name'], '普碳中板')
        self.assertEqual(item['quantity'], '5.0')
        self.assertEqual(item['delivery_location'], '本溪市')
        self.assertIn('三日内供完', item['delivery_period'])
        self.assertEqual(item['source_document_id'], doc['id'])
        self.assertIn(item['quote'], doc['text'])

    def test_scanned_pdf_page_number_is_not_procurement_text(self):
        from types import SimpleNamespace
        page = SimpleNamespace(extract_text=lambda:'',extract_tables=lambda:[])
        class PDF:
            pages = [page]
            def __enter__(self): return self
            def __exit__(self,*args): pass
        with patch('pdfplumber.open',return_value=PDF()):
            doc = parse_document(b'%PDF-fixture',record_id=174,url='https://bid.example/scan.pdf')
        self.assertEqual(doc['status'],'unreadable')
        self.assertEqual(doc['text'],'')

    def test_redirect_is_checked_before_second_request(self):
        class Response:
            status_code = 302
            headers = {'location':'http://127.0.0.1/private.pdf'}
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        class Client:
            urls = []
            def stream(self,method,url,**kwargs):
                self.urls.append(url)
                return Response()
        client = Client()
        with patch('backend.public_documents.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
            with self.assertRaisesRegex(DocumentError,'attachment_outside_source'):
                asyncio.run(_download(client,'https://bid.example/file.pdf','https://bid.example/notice'))
        self.assertEqual(len(client.urls),1)

    def test_stream_limit_does_not_trust_content_length(self):
        class Response:
            status_code = 200
            headers = {}
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def aiter_bytes(self):
                yield b'first'
                yield b'second'
        class Client:
            def stream(self,*args,**kwargs): return Response()
        with patch('backend.public_documents.MAX_BYTES',8), patch('backend.public_documents.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
            with self.assertRaisesRegex(DocumentError,'attachment_too_large'):
                asyncio.run(_download(Client(),'https://bid.example/file.pdf','https://bid.example/notice'))


if __name__ == '__main__':
    unittest.main()
