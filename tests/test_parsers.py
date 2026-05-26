import sys
sys.path.insert(0, "backend")

from llm_client import parse_llm_response, parse_metadata_response


def test_parse_valid_json_array():
    text = '[{"obligation_type": "report", "description": "Presentar informe"}]'
    result = parse_llm_response(text)
    assert len(result) == 1
    assert result[0]["obligation_type"] == "report"


def test_parse_json_with_preamble():
    text = 'Aquí están las obligaciones:\n[{"obligation_type": "compliance", "description": "Cumplir norma"}]'
    result = parse_llm_response(text)
    assert len(result) == 1


def test_parse_empty_array():
    result = parse_llm_response("[]")
    assert result == []


def test_parse_empty_array_with_text():
    result = parse_llm_response("No encontré obligaciones en este fragmento: []")
    assert result == []


def test_parse_invalid_json_returns_empty():
    result = parse_llm_response("Esto no es JSON válido {broken")
    assert result == []


def test_parse_dict_instead_of_list_returns_empty():
    result = parse_llm_response('{"obligation_type": "report"}')
    assert result == []


def test_parse_metadata_valid():
    text = '{"contract_number": "CT-001", "contracting_entity": "Alcaldía", "contractor_name": "Empresa SAS"}'
    result = parse_metadata_response(text)
    assert result["contract_number"] == "CT-001"
    assert result["contractor_name"] == "Empresa SAS"


def test_parse_metadata_with_preamble():
    text = 'Los metadatos son:\n{"contract_number": "CT-002", "contracting_entity": "Gobernación"}'
    result = parse_metadata_response(text)
    assert result["contract_number"] == "CT-002"


def test_parse_metadata_invalid_returns_empty():
    result = parse_metadata_response("texto sin JSON")
    assert result == {}
