# config.py — constants and lookup tables

ADMIN_PHONE = "65993125115"
TEST_PHONES = [ADMIN_PHONE, "6599339250"]  # excluídos de métricas reais

PIN_MAX_TENTATIVAS = 5
PIN_BLOQUEIO_MINUTOS = 15

CATEGORIAS_TERAPEUTICAS = [
    {"id": "diabetes",       "label": "Diabetes",                  "kw": ["metformina","insulina","glibenclamida","sitagliptina","glicazida","dapagliflozina","empagliflozina","acarbose","tiras glicêmicas","glicosímetro"]},
    {"id": "hipertensao",    "label": "Hipertensão",               "kw": ["losartana","enalapril","amlodipina","hidroclorotiazida","captopril","atenolol","olmesartana","valsartana","ramipril","bisoprolol","carvedilol","nifedipino"]},
    {"id": "colesterol",     "label": "Colesterol / Triglicerídeos","kw": ["sinvastatina","atorvastatina","rosuvastatina","ezetimiba","bezafibrato","fenofibrato","pitavastatina"]},
    {"id": "cardio",         "label": "Cardiológico",              "kw": ["ácido acetilsalicílico","aas","clopidogrel","digoxina","amiodarona","varfarina","rivaroxabana","apixabana","dabigatrana","espironolactona","furosemida"]},
    {"id": "tireoide",       "label": "Tireoide",                  "kw": ["levotiroxina","propiltiouracil","metimazol","liotironina"]},
    {"id": "respiratorio",   "label": "Respiratório / Asma",       "kw": ["salbutamol","budesonida","formoterol","montelucaste","salmeterol","fluticasona","tiotrópio","ipratrópio","beclometasona"]},
    {"id": "dor_cronica",    "label": "Dor crônica / Neuropatia", "kw": ["tramadol","codeína","pregabalina","gabapentina","morfina","tapentadol","duloxetina","amitriptilina"]},
    {"id": "antidepressivo", "label": "Antidepressivo / Ansiolítico","kw": ["sertralina","fluoxetina","escitalopram","clonazepam","alprazolam","bupropiona","venlafaxina","paroxetina","mirtazapina"]},
    {"id": "gastro",         "label": "Gastrintestinal",           "kw": ["omeprazol","pantoprazol","esomeprazol","domperidona","metoclopramida","lansoprazol","rabeprazol"]},
    {"id": "geriatrico",     "label": "Geriátrico / Neurológico",  "kw": ["donepezila","memantina","rivastigmina","galantamina","levodopa","pramipexol","rasagilina"]},
    {"id": "dermatologia",   "label": "Dermatologia",              "kw": ["isotretinoína","acitretina","tacrolimus","pimecrolimus","clobetasol","betametasona"]},
    {"id": "outros",         "label": "Outros / Geral",            "kw": []},
]

DDD_ESTADO = {
    11: "SP", 12: "SP", 13: "SP", 14: "SP", 15: "SP", 16: "SP", 17: "SP", 18: "SP", 19: "SP",
    21: "RJ", 22: "RJ", 24: "RJ",
    27: "ES", 28: "ES",
    31: "MG", 32: "MG", 33: "MG", 34: "MG", 35: "MG", 37: "MG", 38: "MG",
    41: "PR", 42: "PR", 43: "PR", 44: "PR", 45: "PR", 46: "PR",
    47: "SC", 48: "SC", 49: "SC",
    51: "RS", 53: "RS", 54: "RS", 55: "RS",
    61: "DF", 62: "GO", 64: "GO",
    63: "TO", 65: "MT", 66: "MT", 67: "MS", 68: "AC", 69: "RO",
    71: "BA", 73: "BA", 74: "BA", 75: "BA", 77: "BA",
    79: "SE", 81: "PE", 87: "PE", 82: "AL", 83: "PB", 84: "RN",
    85: "CE", 88: "CE", 86: "PI", 89: "PI",
    91: "PA", 93: "PA", 94: "PA", 92: "AM", 97: "AM",
    95: "RR", 96: "AP", 98: "MA", 99: "MA"
}
