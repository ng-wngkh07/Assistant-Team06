"""Mentioning an answer or solution does not turn theory into an answer key."""
import pytest
from types import SimpleNamespace
from app.learning import LearningService, _is_theory_chunk


@pytest.mark.parametrize('text', [
    'The solution of a linear equation can be represented by a point on its graph.',
    'A worked exercise can help demonstrate a theorem; this paragraph defines a vector space.',
    'Khi giải phương trình, lời giải phải giữ điều kiện xác định của biểu thức.',
    'Tài liệu minh họa cách xây dựng bài tập từ định nghĩa đạo hàm.',
])
def test_explanatory_theory_is_kept_despite_inline_exercise_words(text):
    assert _is_theory_chunk(text)


@pytest.mark.parametrize('text', [
    'Bài tập\nBài 1. Tính 2 + 3.',
    'Đáp án\n1. 5',
    'Solutions\nProblem 1: 5',
])
def test_actual_exercise_or_answer_sections_are_not_used_as_theory(text):
    assert not _is_theory_chunk(text)


def test_checkpoint_numbered_questions_are_exercises_and_keep_section_numbers():
    learning = LearningService(SimpleNamespace(pdf_path=lambda _: None), SimpleNamespace())
    chunks = [{'id': 1, 'page_num': 4, 'text': 'Checkpoint\n1.1 What is a memory address?\n1.2 Name two kinds of software.'}]
    exercises, _, _ = learning._extract_exercises_and_answers(chunks, {'id': 1, 'filename': 'test.md'})
    assert [x['id'] for x in exercises] == ['1.1', '1.2']
    assert '1.2' not in exercises[0]['question']


def test_preface_describing_checkpoints_is_not_an_exercise():
    learning = LearningService(SimpleNamespace(pdf_path=lambda _: None), SimpleNamespace())
    chunks = [{'id': 1, 'page_num': 1, 'text': 'Preface\nCheckpoints\nCheckpoints are questions placed throughout each chapter as a self-test study aid. Answers are provided separately.'}]
    exercises, _, _ = learning._extract_exercises_and_answers(chunks, {'id': 1, 'filename': 'test.md'})
    assert not exercises


def test_multiline_checkpoint_ends_before_next_numbered_theory_heading():
    learning = LearningService(SimpleNamespace(pdf_path=lambda _: None), SimpleNamespace())
    text = ('Checkpoint\n1.1\nWhat is a memory address?\n'
            '1.2\nName two kinds of software.\n'
            '1.3\nPrograms and Java\n'
            'A program contains instructions that a computer executes.\n')
    exercises, _, _ = learning._extract_exercises_and_answers(
        [{'id': 2, 'page_num': 4, 'text': text}],
        {'id': 1, 'filename': 'test.md'},
    )
    assert [x['id'] for x in exercises] == ['1.1', '1.2']
    assert 'What is a memory address?' in exercises[0]['question']
    assert 'Programs and Java' not in exercises[1]['question']
