import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.graph.tools import request_document

    state = {
        "patient_name": "Isabela Spinelli Ferrari De Siqueira Campos Arruda",
        "patient_age": 21,
        "patient_cpf": "059.787.304-66",
        "preferred_doctor": "julio",
        "financial_name": "João André de Siqueira Campos Arruda",
        "financial_cpf": "714.775.274-00",
        "financial_email": "joaoandrearruda@gmail.com",
    }
    config = {"configurable": {"phone": "5581999655881"}}

    result = await request_document.coroutine(
        document_type="nota_fiscal",
        patient_email="isabelaspinelli9@gmail.com",
        state=state,
        config=config,
        financial_name="João André de Siqueira Campos Arruda",
        financial_cpf="714.775.274-00",
        financial_email="joaoandrearruda@gmail.com",
    )
    print(result)

asyncio.run(main())
