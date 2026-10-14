"""Answer-bearing concepts must enter the candidate pool ahead of unrelated word matches."""
import pytest
from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher, is_heading_or_toc_chunk


def test_spaced_dot_leaders_are_a_table_of_contents():
    source='3. Đạo hàm, vi phân và ứng dụng\n' + '. '*45 + '10\n3.1. Định nghĩa, ý nghĩa và bảng đạo hàm\n' + '. '*45 + '10\n3.2. Đạo hàm cấp cao và công thức Leibniz\n'+'. '*45+'11\n'
    assert is_heading_or_toc_chunk(source)


@pytest.mark.parametrize('concept',['đạo hàm','mệnh đề','điều kiện hóa cổ điển'])
def test_concept_phrase_candidate_survives_generic_word_matches(tmp_path,concept):
    src=tmp_path/'src';src.mkdir()
    words=concept.split()
    unrelated=' '.join(f'{w} là gì trong một ngữ cảnh khác.' for w in words)
    for i in range(65):
        (src/f'distractor-{i}.txt').write_text((unrelated+'\n')*6)
    evidence=f'{concept.capitalize()} được định nghĩa là một khái niệm chuyên môn có điều kiện áp dụng được nêu rõ trong phần văn bản này. Đây là đoạn giải thích trực tiếp, các từ trong tên khái niệm phải đi cùng nhau.'
    (src/'evidence.txt').write_text(evidence)
    idx=KnowledgeIndexer(tmp_path/'kb.db',src);idx.index_all(embed_model=None)
    found=HybridSearcher(tmp_path/'kb.db').search_hybrid(f'{concept} là gì',embed_model=None,top_k=6)
    assert found[0]['filename']=='evidence.txt'
    assert concept in found[0]['text'].casefold()


@pytest.mark.parametrize('concept',['đạo hàm','mệnh đề','điều kiện hóa cổ điển'])
@pytest.mark.parametrize('intent',['{} là gì','lý thuyết {}'])
def test_course_catalog_does_not_outrank_local_explanation(tmp_path,concept,intent):
    src=tmp_path/'src';src.mkdir()
    # Both documents contain the concept and definition cues. The catalogue's
    # cues describe other chapters; it does not explain the requested concept.
    catalog=('Danh sách bài học và tài nguyên.\n'
             'Tuần 1: Định nghĩa và tính chất các khái niệm mở đầu.\n'+
             'Mô tả tài liệu bổ sung và lịch trình học. '*14+'\n'+
             f'Tuần 6: {concept}. Video ôn tập, bài tập và lịch học.\n'+
             'Các phần sau giới thiệu công thức và định nghĩa của chương khác.')
    evidence=(f'3. {concept.capitalize()}\n3.1. Định nghĩa và ý nghĩa\n'
              'Đây là phần giải thích trực tiếp nội dung khái niệm đang được hỏi, '
              'nêu ý nghĩa, điều kiện áp dụng và các thành phần của định nghĩa. '
              'Phần trình bày này cung cấp kiến thức, không chỉ thông báo lịch học.')
    (src/'catalog.txt').write_text(catalog)
    (src/'definition.txt').write_text(evidence)
    idx=KnowledgeIndexer(tmp_path/'kb.db',src);idx.index_all(embed_model=None)
    found=HybridSearcher(tmp_path/'kb.db').search_hybrid(intent.format(concept),embed_model=None,top_k=2)
    assert found[0]['filename']=='definition.txt'
